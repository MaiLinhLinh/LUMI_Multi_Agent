import pytest

from rag_manager.services.music_repository import (
    MusicChromaRecord,
    MusicChromaRepository,
)


class FakeCollection:
    def __init__(self) -> None:
        self.upserts = []

    def count(self):
        return 2

    def get(self, **_kwargs):
        return {
            "ids": ["youtube_12345678901"],
            "documents": ["song artist"],
            "embeddings": [[0.1, 0.2]],
            "metadatas": [{"title": "Song"}],
        }

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def query(self, **_kwargs):
        return {
            "ids": [["youtube_12345678901"]],
            "documents": [["song artist"]],
            "metadatas": [[{"title": "Song"}]],
            "distances": [[0.25]],
        }


class FakeClient:
    def __init__(self) -> None:
        self.collection = FakeCollection()
        self.request = None

    def get_or_create_collection(self, **kwargs):
        self.request = kwargs
        return self.collection


def test_music_repository_creates_cosine_collection_and_upserts() -> None:
    client = FakeClient()
    repository = MusicChromaRepository(
        client=client,
        embedding_dimensions=2,
    )
    record = MusicChromaRecord(
        id="youtube_12345678901",
        document="song artist",
        embedding=[0.1, 0.2],
        metadata={"title": "Song"},
    )

    assert repository.upsert([record]) == 1

    assert client.request == {
        "name": "music_tracks_v1",
        "configuration": {"hnsw": {"space": "cosine"}},
    }
    assert client.collection.upserts[0]["ids"] == [record.id]
    assert repository.count() == 2


def test_music_repository_returns_existing_embedding() -> None:
    repository = MusicChromaRepository(client=FakeClient(), embedding_dimensions=2)

    existing = repository.get_existing(["youtube_12345678901"])

    assert existing["youtube_12345678901"].embedding == [0.1, 0.2]
    assert existing["youtube_12345678901"].metadata["title"] == "Song"


def test_music_repository_queries_with_caller_supplied_embedding() -> None:
    client = FakeClient()
    repository = MusicChromaRepository(client=client, embedding_dimensions=2)

    records = repository.query_by_embedding(
        [0.1, 0.2],
        where={"embeddable": {"$eq": True}},
    )

    assert records[0].id == "youtube_12345678901"
    assert records[0].distance == 0.25
    assert records[0].metadata["title"] == "Song"


def test_music_repository_reads_documents_for_bm25() -> None:
    repository = MusicChromaRepository(client=FakeClient(), embedding_dimensions=2)

    records = repository.get_records(where={"embeddable": {"$eq": True}})

    assert records[0].document == "song artist"


def test_music_repository_rejects_wrong_embedding_dimensions() -> None:
    repository = MusicChromaRepository(client=FakeClient(), embedding_dimensions=2)
    record = MusicChromaRecord(
        id="youtube_12345678901",
        document="song artist",
        embedding=[0.1],
        metadata={"title": "Song"},
    )

    with pytest.raises(ValueError, match="must have 2 dimensions"):
        repository.upsert([record])


def test_music_repository_persists_with_real_chroma(tmp_path) -> None:
    pytest.importorskip("chromadb")
    path = tmp_path / "chroma_music"
    repository = MusicChromaRepository(path=path, embedding_dimensions=4)
    record = MusicChromaRecord(
        id="youtube_AbCdEfGhI12",
        document="Tên bài hát | Nghệ sĩ",
        embedding=[0.1, 0.2, 0.3, 0.4],
        metadata={
            "title": "Tên bài hát",
            "artist_names": ["Nghệ sĩ"],
            "embeddable": True,
        },
    )

    repository.upsert([record])
    reopened = MusicChromaRepository(path=path, embedding_dimensions=4)
    existing = reopened.get_existing([record.id])

    assert path.exists()
    assert reopened.count() == 1
    assert existing[record.id].document == record.document
    assert existing[record.id].metadata["artist_names"] == ["Nghệ sĩ"]
    assert existing[record.id].embedding == pytest.approx(record.embedding)
