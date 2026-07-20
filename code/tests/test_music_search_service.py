import pytest

from rag_manager.services.music_repository import MusicSearchRecord
from rag_manager.services.music_search_service import (
    MusicSearchService,
    RankedRecord,
    normalize_music_text,
    reciprocal_rank_fusion,
)


def _record(
    record_id: str,
    title: str,
    *,
    view_count: int,
    release_epoch: int,
    content_type: str = "official_mv",
    distance: float | None = None,
):
    normalized = normalize_music_text(title)
    return MusicSearchRecord(
        id=record_id,
        document=f"{title} | {normalized} | Sơn Tùng M-TP | son tung m tp | vi",
        distance=distance,
        metadata={
            "track_id": f"track_{record_id}",
            "title": title,
            "normalized_title": normalized,
            "artist_names": ["Sơn Tùng M-TP"],
            "artist_keys": ["son tung m tp"],
            "language": "vi",
            "video_id": record_id[-11:],
            "content_type": content_type,
            "version": "official MV" if content_type == "official_mv" else content_type,
            "thumbnail_url": "",
            "duration_seconds": 240,
            "release_date": "2024-01-01",
            "release_date_epoch": release_epoch,
            "release_date_origin": "youtube_published_at_proxy",
            "popularity_score": float(view_count),
            "view_count": view_count,
        },
    )


LAC_TROI = _record(
    "youtube_Llw9Q6akRo4",
    "Lạc Trôi",
    view_count=200,
    release_epoch=100,
    distance=0.3,
)
NOI_NAY = _record(
    "youtube_FN7ALfpGxiI",
    "Nơi Này Có Anh",
    view_count=500,
    release_epoch=200,
    distance=0.1,
)


class FakeRepository:
    def __init__(self):
        self.records = [LAC_TROI, NOI_NAY]
        self.query_calls = []

    def get_records(self, *, where=None, limit=None):
        return list(self.records)

    def query_by_embedding(self, embedding, *, limit, where=None):
        self.query_calls.append({"embedding": embedding, "limit": limit, "where": where})
        return [NOI_NAY, LAC_TROI]


class FakeEmbeddingService:
    def __init__(self):
        self.calls = []

    def embed_documents(self, texts, *, batch_size):
        self.calls.append((list(texts), batch_size))
        return [[0.1, 0.2] for _ in texts]


def _service():
    repository = FakeRepository()
    embeddings = FakeEmbeddingService()
    service = MusicSearchService(
        repository=repository,
        embedding_service=embeddings,
        dense_top_k=10,
        bm25_top_k=10,
        output_top_k=5,
    )
    return service, repository, embeddings


def test_hybrid_search_exact_title_wins_and_embedding_is_cached() -> None:
    service, _repository, embeddings = _service()
    extraction = {
        "search_query": "bật Lạc Trôi",
        "title": "Lạc Trôi",
        "artist": "Sơn Tùng",
    }

    first = service.search(extraction)
    second = service.search(extraction)

    assert first["strategy"] == "hybrid_rrf"
    assert first["candidates"][0]["title"] == "Lạc Trôi"
    assert first["candidates"][0]["ranking"]["exact_boost"] == pytest.approx(0.06)
    assert first["diagnostics"]["embedding_cache_hit"] is False
    assert second["diagnostics"]["embedding_cache_hit"] is True
    assert len(embeddings.calls) == 1


def test_structured_popularity_sort_does_not_call_embedding() -> None:
    service, _repository, embeddings = _service()

    result = service.search(
        {
            "artist": "Sơn Tùng",
            "sort_by": "popularity",
            "sort_order": "desc",
        }
    )

    assert result["strategy"] == "structured_sort"
    assert [item["title"] for item in result["candidates"]] == [
        "Nơi Này Có Anh",
        "Lạc Trôi",
    ]
    assert embeddings.calls == []


def test_structured_latest_sort_uses_release_epoch_not_similarity() -> None:
    service, _repository, _embeddings = _service()

    result = service.search(
        {
            "artist": "Sơn Tùng",
            "sort_by": "release_date",
            "sort_order": "desc",
        }
    )

    assert result["candidates"][0]["title"] == "Nơi Này Có Anh"
    assert result["candidates"][0]["release_date_origin"] == (
        "youtube_published_at_proxy"
    )


def test_unavailable_version_is_filtered_before_retrieval() -> None:
    service, repository, embeddings = _service()

    result = service.search(
        {
            "search_query": "Lạc Trôi live",
            "title": "Lạc Trôi",
            "artist": "Sơn Tùng",
            "version": "live",
        }
    )

    assert result["candidates"] == []
    assert repository.query_calls == []
    assert embeddings.calls == []


def test_rrf_uses_ranks_not_raw_score_scales() -> None:
    dense = [RankedRecord(NOI_NAY, rank=1, raw_score=99999.0)]
    bm25 = [RankedRecord(LAC_TROI, rank=1, raw_score=0.000001)]

    fused = reciprocal_rank_fusion(dense, bm25, rrf_k=60)

    assert all(item["rrf_score"] == pytest.approx(1 / 61) for item in fused)


def test_normalization_matches_vietnamese_with_or_without_accents() -> None:
    assert normalize_music_text("Đừng Làm Trái Tim Anh Đau") == (
        "dung lam trai tim anh dau"
    )


def test_artist_filter_accepts_mtp_with_or_without_hyphen() -> None:
    service, _repository, _embeddings = _service()

    result = service.search(
        {
            "artist": "Sơn Tùng MTP",
            "sort_by": "popularity",
            "sort_order": "desc",
        }
    )

    assert len(result["candidates"]) == 2


def test_language_filter_maps_tieng_viet_to_vi() -> None:
    service, _repository, _embeddings = _service()

    result = service.search(
        {
            "language": "tiếng Việt",
            "sort_by": "popularity",
            "sort_order": "desc",
        }
    )

    assert len(result["candidates"]) == 2
