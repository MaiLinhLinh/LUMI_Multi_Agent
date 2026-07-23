"""ChromaDB persistence primitives shared by Music workers and search."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


MUSIC_COLLECTION_NAME = "music_tracks_v1"
MUSIC_EMBEDDING_DIMENSIONS = 1024


@dataclass(frozen=True)
class MusicChromaRecord:
    id: str
    document: str
    embedding: list[float]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MusicSearchRecord:
    id: str
    document: str
    metadata: dict[str, Any]
    distance: float | None = None


class MusicChromaRepository:
    """Own the local Chroma collection without exposing raw filters to LLMs."""

    def __init__(
        self,
        *,
        path: str | Path = "data/chroma_music",
        collection_name: str = MUSIC_COLLECTION_NAME,
        embedding_dimensions: int = MUSIC_EMBEDDING_DIMENSIONS,
        client: Any | None = None,
    ) -> None:
        self.path = Path(path)
        self.collection_name = collection_name
        self.embedding_dimensions = embedding_dimensions
        if client is None:
            try:
                import chromadb
            except ImportError as exc:  # pragma: no cover - environment guard
                raise RuntimeError(
                    "ChromaDB is not installed. Run: pip install -r requirements.txt"
                ) from exc
            client = chromadb.PersistentClient(path=str(self.path))
        self._client = client
        self._collection = client.get_or_create_collection(
            name=collection_name,
            configuration={"hnsw": {"space": "cosine"}},
        )
        configuration = getattr(self._collection, "configuration", None)
        if isinstance(configuration, dict):
            hnsw = configuration.get("hnsw")
            space = hnsw.get("space") if isinstance(hnsw, dict) else None
            if space is not None and space != "cosine":
                raise RuntimeError(
                    f"Existing Chroma collection {collection_name!r} uses "
                    f"distance {space!r}, expected 'cosine'."
                )

    def count(self) -> int:
        return int(self._collection.count())

    def get_existing(self, ids: Iterable[str]) -> dict[str, MusicChromaRecord]:
        requested = list(dict.fromkeys(str(value) for value in ids if str(value)))
        if not requested:
            return {}
        payload = self._collection.get(
            ids=requested,
            include=["documents", "embeddings", "metadatas"],
        )
        result: dict[str, MusicChromaRecord] = {}
        returned_ids = payload.get("ids") or []
        documents = payload.get("documents") or []
        embeddings = payload.get("embeddings")
        metadatas = payload.get("metadatas") or []
        if embeddings is None:
            embeddings = []
        for index, record_id in enumerate(returned_ids):
            raw_embedding = embeddings[index] if index < len(embeddings) else []
            embedding = [float(value) for value in raw_embedding]
            metadata = metadatas[index] if index < len(metadatas) else {}
            result[str(record_id)] = MusicChromaRecord(
                id=str(record_id),
                document=str(documents[index]) if index < len(documents) else "",
                embedding=embedding,
                metadata=dict(metadata or {}),
            )
        return result

    def upsert(
        self,
        records: Iterable[MusicChromaRecord],
        *,
        batch_size: int = 100,
    ) -> int:
        if batch_size <= 0:
            raise ValueError("Chroma upsert batch size must be positive")
        items = list(records)
        for record in items:
            _validate_record(record, dimensions=self.embedding_dimensions)
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            self._collection.upsert(
                ids=[record.id for record in batch],
                documents=[record.document for record in batch],
                embeddings=[record.embedding for record in batch],
                metadatas=[record.metadata for record in batch],
            )
        return len(items)

    def query_by_embedding(
        self,
        embedding: Iterable[float],
        *,
        limit: int = 50,
        where: Mapping[str, Any] | None = None,
    ) -> list[MusicSearchRecord]:
        """Return cosine-nearest records for a caller-supplied BGE embedding."""

        vector = [float(value) for value in embedding]
        if len(vector) != self.embedding_dimensions:
            raise ValueError(
                f"Music query embedding must have {self.embedding_dimensions} dimensions"
            )
        if limit <= 0:
            raise ValueError("Music dense search limit must be positive")
        kwargs: dict[str, Any] = {
            "query_embeddings": [vector],
            "n_results": min(limit, max(1, self.count())),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = dict(where)
        payload = self._collection.query(**kwargs)
        ids = _first_query_list(payload.get("ids"))
        documents = _first_query_list(payload.get("documents"))
        metadatas = _first_query_list(payload.get("metadatas"))
        distances = _first_query_list(payload.get("distances"))
        records: list[MusicSearchRecord] = []
        for index, record_id in enumerate(ids):
            raw_distance = distances[index] if index < len(distances) else None
            records.append(
                MusicSearchRecord(
                    id=str(record_id),
                    document=(
                        str(documents[index]) if index < len(documents) else ""
                    ),
                    metadata=dict(
                        metadatas[index]
                        if index < len(metadatas)
                        and isinstance(metadatas[index], Mapping)
                        else {}
                    ),
                    distance=(
                        float(raw_distance)
                        if isinstance(raw_distance, (int, float))
                        and not isinstance(raw_distance, bool)
                        else None
                    ),
                )
            )
        return records

    def get_records(
        self,
        *,
        where: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[MusicSearchRecord]:
        """Read documents and flat metadata for BM25 or structured sorting."""

        if limit is not None and limit <= 0:
            raise ValueError("Music record limit must be positive")
        kwargs: dict[str, Any] = {
            "include": ["documents", "metadatas"],
        }
        if where:
            kwargs["where"] = dict(where)
        if limit is not None:
            kwargs["limit"] = limit
        payload = self._collection.get(**kwargs)
        ids = payload.get("ids") or []
        documents = payload.get("documents") or []
        metadatas = payload.get("metadatas") or []
        return [
            MusicSearchRecord(
                id=str(record_id),
                document=str(documents[index]) if index < len(documents) else "",
                metadata=dict(
                    metadatas[index]
                    if index < len(metadatas)
                    and isinstance(metadatas[index], Mapping)
                    else {}
                ),
            )
            for index, record_id in enumerate(ids)
        ]


def _validate_record(record: MusicChromaRecord, *, dimensions: int) -> None:
    if not record.id.strip():
        raise ValueError("Music Chroma record id must not be empty")
    if not record.document.strip():
        raise ValueError(f"Music Chroma record {record.id!r} has an empty document")
    if len(record.embedding) != dimensions:
        raise ValueError(
            f"Music Chroma record {record.id!r} must have {dimensions} dimensions"
        )
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in record.embedding
    ):
        raise ValueError(f"Music Chroma record {record.id!r} has invalid embedding")
    if not isinstance(record.metadata, Mapping) or not record.metadata:
        raise ValueError(f"Music Chroma record {record.id!r} has empty metadata")


def _first_query_list(raw: Any) -> list[Any]:
    if not isinstance(raw, list) or not raw:
        return []
    first = raw[0]
    return list(first) if isinstance(first, (list, tuple)) else []
