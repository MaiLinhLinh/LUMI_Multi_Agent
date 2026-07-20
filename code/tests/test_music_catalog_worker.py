import json

import pytest

from rag_manager.config import Settings
from rag_manager.services.music_catalog_worker import (
    MusicCatalogError,
    import_music_catalog,
    load_music_catalog,
)
from rag_manager.services.music_repository import MusicChromaRecord


def _catalog(*, video_id: str = "AbCdEfGhI12") -> dict:
    return {
        "catalog_version": "music.catalog.v1",
        "tracks": [
            {
                "title": "Chúng ta của tương lai",
                "artists": ["Sơn Tùng M-TP"],
                "genres": ["V-Pop"],
                "moods": [],
                "language": "vi",
                "tags": ["nhạc Việt"],
                "release_date": "2024-03-08",
                "popularity_score": 90,
                "sources": [
                    {
                        "video_id": video_id,
                        "content_type": "official_mv",
                        "channel_id": "UCexample",
                        "channel_name": "Sơn Tùng M-TP Official",
                        "is_official": True,
                        "embeddable": True,
                        "published_at": "2024-03-08T12:00:00+07:00",
                        "thumbnail_url": (
                            f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                        ),
                        "duration_seconds": 285,
                    }
                ],
            }
        ],
    }


def _write_catalog(tmp_path, payload: dict):
    path = tmp_path / "music.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _settings(**overrides) -> Settings:
    values = {
        "gemini_api_key": "",
        "gemini_base_url": "",
        "gemini_model": "",
        "openweather_api_key": "",
        "gnews_api_key": "",
        "weather_cache_ttl_seconds": 3600,
        "news_cache_ttl_seconds": 900,
        "wiki_cache_ttl_seconds": None,
        "request_timeout_seconds": 8,
        "debug_routing": False,
        "music_embedding_dimensions": 4,
        "music_embedding_batch_size": 2,
    }
    values.update(overrides)
    return Settings(**values)


class FakeEmbeddingService:
    def __init__(self) -> None:
        self.calls = []

    def embed_documents(self, texts, *, batch_size):
        self.calls.append((list(texts), batch_size))
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class FakeRepository:
    def __init__(self, existing=None) -> None:
        self.existing = existing or {}
        self.saved = []

    def get_existing(self, _ids):
        return self.existing

    def upsert(self, records):
        self.saved = list(records)
        return len(self.saved)

    def count(self):
        return len(self.saved)


class FakeYouTubeService:
    def fetch_videos(self, video_ids):
        assert list(video_ids) == ["AbCdEfGhI12"]
        return {
            "AbCdEfGhI12": {
                "channel_id": "UCverified",
                "channel_name": "Verified channel",
                "published_at": "2024-03-09T00:00:00Z",
                "thumbnail_url": "https://i.ytimg.com/vi/AbCdEfGhI12/maxresdefault.jpg",
                "duration_seconds": 300,
                "embeddable": False,
                "source_active": True,
            }
        }


def test_load_catalog_normalizes_dates_and_omits_empty_metadata_arrays(tmp_path) -> None:
    path = _write_catalog(tmp_path, _catalog())

    records = load_music_catalog(path)

    assert len(records) == 1
    record = records[0]
    assert record.id == "youtube_AbCdEfGhI12"
    assert record.metadata["normalized_title"] == "chung ta cua tuong lai"
    assert record.metadata["artist_keys"] == ["son tung m tp"]
    assert record.metadata["release_date"] == "2024-03-08"
    assert record.metadata["published_at"] == "2024-03-08T05:00:00Z"
    assert "moods" not in record.metadata
    assert "Chúng ta của tương lai" in record.document


def test_load_catalog_rejects_malformed_youtube_video_id(tmp_path) -> None:
    path = _write_catalog(tmp_path, _catalog(video_id="not-a-video-id"))

    with pytest.raises(MusicCatalogError, match="11-character YouTube video id"):
        load_music_catalog(path)


def test_dry_run_does_not_call_repository_or_ollama(tmp_path) -> None:
    path = _write_catalog(tmp_path, _catalog())

    result = import_music_catalog(path, settings=_settings(), dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["validated_records"] == 1


def test_import_embeds_and_upserts_validated_records(tmp_path) -> None:
    path = _write_catalog(tmp_path, _catalog())
    repository = FakeRepository()
    embeddings = FakeEmbeddingService()

    result = import_music_catalog(
        path,
        settings=_settings(),
        repository=repository,
        embedding_service=embeddings,
    )

    assert result["embedded_records"] == 1
    assert result["reused_embeddings"] == 0
    assert len(repository.saved) == 1
    assert repository.saved[0].embedding == [0.1, 0.2, 0.3, 0.4]
    assert repository.saved[0].metadata["embedding_model"] == "bge-m3"
    assert embeddings.calls[0][1] == 2


def test_import_reuses_unchanged_embedding_and_created_at(tmp_path) -> None:
    path = _write_catalog(tmp_path, _catalog())
    prepared = load_music_catalog(path)[0]
    old = MusicChromaRecord(
        id=prepared.id,
        document=prepared.document,
        embedding=[0.4, 0.3, 0.2, 0.1],
        metadata={
            **prepared.metadata,
            "embedding_model": "bge-m3",
            "embedding_version": 1,
            "created_at": "2025-01-01T00:00:00Z",
        },
    )
    repository = FakeRepository(existing={prepared.id: old})
    embeddings = FakeEmbeddingService()

    result = import_music_catalog(
        path,
        settings=_settings(),
        repository=repository,
        embedding_service=embeddings,
    )

    assert result["embedded_records"] == 0
    assert result["reused_embeddings"] == 1
    assert embeddings.calls == []
    assert repository.saved[0].embedding == old.embedding
    assert repository.saved[0].metadata["created_at"] == "2025-01-01T00:00:00Z"


def test_import_can_verify_source_facts_without_changing_canonical_track(tmp_path) -> None:
    path = _write_catalog(tmp_path, _catalog())
    repository = FakeRepository()

    result = import_music_catalog(
        path,
        settings=_settings(),
        repository=repository,
        embedding_service=FakeEmbeddingService(),
        youtube_service=FakeYouTubeService(),
        verify_youtube=True,
    )

    metadata = repository.saved[0].metadata
    assert result["ok"] is True
    assert metadata["title"] == "Chúng ta của tương lai"
    assert metadata["release_date"] == "2024-03-08"
    assert metadata["channel_id"] == "UCverified"
    assert metadata["duration_seconds"] == 300
    assert metadata["embeddable"] is False
    assert metadata["youtube_status"] == "available"
