"""Hybrid Music retrieval: BGE-M3 dense search, BM25 and reciprocal-rank fusion."""

from __future__ import annotations

import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from rank_bm25 import BM25Okapi

from rag_manager.services.music_embedding_service import (
    OllamaMusicEmbeddingService,
)
from rag_manager.services.music_repository import (
    MusicChromaRepository,
    MusicSearchRecord,
)


ACTIVE_MUSIC_WHERE: dict[str, Any] = {
    "$and": [
        {"track_active": {"$eq": True}},
        {"source_active": {"$eq": True}},
        {"embeddable": {"$eq": True}},
    ]
}
VERSION_CONTENT_TYPES = {
    "mv": "official_mv",
    "official mv": "official_mv",
    "music video": "official_mv",
    "official music video": "official_mv",
    "audio": "official_audio",
    "official audio": "official_audio",
    "lyric": "lyric_video",
    "lyric video": "lyric_video",
    "lyrics": "lyric_video",
    "live": "live",
    "remix": "remix",
    "acoustic": "acoustic",
    "performance": "performance",
    "karaoke": "karaoke",
}
LANGUAGE_ALIASES = {
    "vi": "vi",
    "vietnamese": "vi",
    "tieng viet": "vi",
    "en": "en",
    "english": "en",
    "tieng anh": "en",
    "ko": "ko",
    "korean": "ko",
    "tieng han": "ko",
    "ja": "ja",
    "japanese": "ja",
    "tieng nhat": "ja",
    "zh": "zh",
    "chinese": "zh",
    "tieng trung": "zh",
}
_TITLE_ALIAS_SEPARATOR = re.compile(r"\s*[|｜]\s*")


@dataclass(frozen=True)
class RankedRecord:
    record: MusicSearchRecord
    rank: int
    raw_score: float | None = None


class QueryEmbeddingCache:
    """Small process-local LRU for repeated short query embeddings."""

    def __init__(self, max_size: int = 128) -> None:
        if max_size <= 0:
            raise ValueError("Query embedding cache size must be positive")
        self.max_size = max_size
        self._values: OrderedDict[str, list[float]] = OrderedDict()

    def get(self, key: str) -> list[float] | None:
        value = self._values.get(key)
        if value is None:
            return None
        self._values.move_to_end(key)
        return list(value)

    def set(self, key: str, value: Sequence[float]) -> None:
        self._values[key] = [float(item) for item in value]
        self._values.move_to_end(key)
        while len(self._values) > self.max_size:
            self._values.popitem(last=False)


class MusicBm25Index:
    """In-memory BM25 index over the exact documents stored in Chroma."""

    def __init__(self, records: Sequence[MusicSearchRecord]) -> None:
        self.records = list(records)
        tokenized = [tokenize_music_text(record.document) for record in self.records]
        self._model = BM25Okapi(tokenized) if tokenized else None

    def search(
        self,
        query: str,
        *,
        allowed_ids: set[str] | None = None,
        limit: int = 50,
    ) -> list[RankedRecord]:
        if self._model is None or limit <= 0:
            return []
        tokens = tokenize_music_text(query)
        if not tokens:
            return []
        scores = self._model.get_scores(tokens)
        candidates: list[tuple[MusicSearchRecord, float]] = []
        for index, record in enumerate(self.records):
            if allowed_ids is not None and record.id not in allowed_ids:
                continue
            score = float(scores[index])
            if score > 0:
                candidates.append((record, score))
        candidates.sort(key=lambda item: (item[1], item[0].id), reverse=True)
        return [
            RankedRecord(record=record, rank=index, raw_score=score)
            for index, (record, score) in enumerate(candidates[:limit], start=1)
        ]


class MusicSearchService:
    """Execute deterministic structured sorting or hybrid retrieval."""

    def __init__(
        self,
        *,
        repository: MusicChromaRepository,
        embedding_service: OllamaMusicEmbeddingService,
        dense_top_k: int = 50,
        bm25_top_k: int = 50,
        rrf_k: int = 60,
        output_top_k: int = 5,
        embedding_cache_size: int = 128,
    ) -> None:
        if min(dense_top_k, bm25_top_k, rrf_k, output_top_k) <= 0:
            raise ValueError("Music search limits and rrf_k must be positive")
        self.repository = repository
        self.embedding_service = embedding_service
        self.dense_top_k = dense_top_k
        self.bm25_top_k = bm25_top_k
        self.rrf_k = rrf_k
        self.output_top_k = output_top_k
        self.embedding_cache = QueryEmbeddingCache(embedding_cache_size)
        self._records: list[MusicSearchRecord] = []
        self._bm25 = MusicBm25Index([])
        self.refresh_index()

    def refresh_index(self) -> int:
        """Reload active Chroma documents after a catalog worker update."""

        self._records = self.repository.get_records(where=ACTIVE_MUSIC_WHERE)
        self._bm25 = MusicBm25Index(self._records)
        return len(self._records)

    def search(
        self,
        extraction: Mapping[str, Any],
        *,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        result_limit = top_k or self.output_top_k
        if result_limit <= 0:
            raise ValueError("Music result top_k must be positive")
        filtered = [
            record
            for record in self._records
            if _matches_structured_filters(record, extraction)
        ]
        query = build_music_search_query(extraction)
        sort_by = _optional_text(extraction.get("sort_by"))
        if sort_by in {"release_date", "popularity"}:
            candidates = _structured_sort(
                filtered,
                sort_by=sort_by,
                sort_order=_optional_text(extraction.get("sort_order")) or "desc",
            )[:result_limit]
            return {
                "strategy": "structured_sort",
                "query": query,
                "candidates": [
                    _candidate_payload(record, final_score=None) for record in candidates
                ],
                "diagnostics": {
                    "catalog_records": len(self._records),
                    "filtered_records": len(filtered),
                    "sort_by": sort_by,
                    "sort_order": _optional_text(extraction.get("sort_order"))
                    or "desc",
                    "embedding_cache_hit": None,
                    "elapsed_seconds": time.perf_counter() - started,
                },
            }

        if not query or not filtered:
            return {
                "strategy": "hybrid_rrf",
                "query": query,
                "candidates": [],
                "diagnostics": {
                    "catalog_records": len(self._records),
                    "filtered_records": len(filtered),
                    "dense_candidates": 0,
                    "bm25_candidates": 0,
                    "embedding_cache_hit": None,
                    "elapsed_seconds": time.perf_counter() - started,
                },
            }

        allowed_ids = {record.id for record in filtered}
        query_key = normalize_music_text(query)
        embedding = self.embedding_cache.get(query_key)
        cache_hit = embedding is not None
        if embedding is None:
            embedding = self.embedding_service.embed_documents([query], batch_size=1)[0]
            self.embedding_cache.set(query_key, embedding)
        dense_records = self.repository.query_by_embedding(
            embedding,
            limit=min(max(self.dense_top_k, len(filtered)), max(1, len(self._records))),
            where=ACTIVE_MUSIC_WHERE,
        )
        dense_ranked = [
            RankedRecord(record=record, rank=rank, raw_score=record.distance)
            for rank, record in enumerate(
                (record for record in dense_records if record.id in allowed_ids),
                start=1,
            )
        ][: self.dense_top_k]
        bm25_ranked = self._bm25.search(
            query,
            allowed_ids=allowed_ids,
            limit=self.bm25_top_k,
        )
        fused = reciprocal_rank_fusion(
            dense_ranked,
            bm25_ranked,
            rrf_k=self.rrf_k,
            extraction=extraction,
        )
        candidates = [
            _candidate_payload(
                item["record"],
                final_score=item["final_score"],
                dense_rank=item["dense_rank"],
                bm25_rank=item["bm25_rank"],
                rrf_score=item["rrf_score"],
                exact_boost=item["exact_boost"],
            )
            for item in fused[:result_limit]
        ]
        return {
            "strategy": "hybrid_rrf",
            "query": query,
            "candidates": candidates,
            "diagnostics": {
                "catalog_records": len(self._records),
                "filtered_records": len(filtered),
                "dense_candidates": len(dense_ranked),
                "bm25_candidates": len(bm25_ranked),
                "rrf_k": self.rrf_k,
                "embedding_cache_hit": cache_hit,
                "elapsed_seconds": time.perf_counter() - started,
            },
        }


def reciprocal_rank_fusion(
    dense: Sequence[RankedRecord],
    bm25: Sequence[RankedRecord],
    *,
    rrf_k: int = 60,
    extraction: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fuse ranks only; raw cosine/BM25 scores are intentionally not combined."""

    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    extraction = extraction or {}
    fused: dict[str, dict[str, Any]] = {}
    for source_name, rankings in (("dense", dense), ("bm25", bm25)):
        for item in rankings:
            entry = fused.setdefault(
                item.record.id,
                {
                    "record": item.record,
                    "dense_rank": None,
                    "bm25_rank": None,
                    "rrf_score": 0.0,
                },
            )
            entry[f"{source_name}_rank"] = item.rank
            entry["rrf_score"] += 1.0 / (rrf_k + item.rank)
    for entry in fused.values():
        boost = _exact_match_boost(entry["record"], extraction)
        entry["exact_boost"] = boost
        entry["final_score"] = entry["rrf_score"] + boost
    return sorted(
        fused.values(),
        key=lambda item: (
            item["final_score"],
            int(item["record"].metadata.get("view_count", 0)),
            item["record"].id,
        ),
        reverse=True,
    )


def build_music_search_query(extraction: Mapping[str, Any]) -> str:
    fields = (
        "title",
        "artist",
        "genre",
        "mood",
        "language",
        "version",
        "search_query",
    )
    values: list[str] = []
    seen: set[str] = set()
    for field in fields:
        value = _optional_text(extraction.get(field))
        key = normalize_music_text(value)
        if value and key and key not in seen:
            seen.add(key)
            values.append(value)
    return " ".join(values)


def normalize_music_text(value: str) -> str:
    decomposed = unicodedata.normalize(
        "NFKD", value.replace("Đ", "D").replace("đ", "d")
    )
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", without_marks.casefold()).strip()


def music_title_aliases(
    title: str,
    explicit_aliases: Sequence[Any] | None = None,
) -> tuple[str, ...]:
    """Return normalized full-title and bilingual-title aliases."""

    raw_values = [title]
    raw_values.extend(_TITLE_ALIAS_SEPARATOR.split(title))
    if explicit_aliases:
        raw_values.extend(str(value) for value in explicit_aliases)
    aliases: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        normalized = normalize_music_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            aliases.append(normalized)
    return tuple(aliases)


def tokenize_music_text(value: str) -> list[str]:
    return normalize_music_text(value).split()


def _matches_structured_filters(
    record: MusicSearchRecord,
    extraction: Mapping[str, Any],
) -> bool:
    metadata = record.metadata
    artist = normalize_music_text(_optional_text(extraction.get("artist")))
    if artist and not any(
        _tokens_compatible(artist, normalize_music_text(str(candidate)))
        for candidate in metadata.get("artist_keys", [])
    ):
        return False
    title = normalize_music_text(_optional_text(extraction.get("title")))
    observed_title = normalize_music_text(str(metadata.get("normalized_title", "")))
    if title and not _tokens_compatible(title, observed_title):
        return False
    language = _normalize_language(_optional_text(extraction.get("language")))
    if language and language != _normalize_language(
        str(metadata.get("language", ""))
    ):
        return False
    for field, metadata_field in (("genre", "genres"), ("mood", "moods")):
        requested = normalize_music_text(_optional_text(extraction.get(field)))
        observed = metadata.get(metadata_field, [])
        if requested and not any(
            _tokens_compatible(requested, normalize_music_text(str(value)))
            for value in observed
        ):
            return False
    version = normalize_music_text(_optional_text(extraction.get("version")))
    if version:
        expected_type = VERSION_CONTENT_TYPES.get(version)
        if expected_type is None:
            return False
        if metadata.get("content_type") != expected_type:
            return False
    return True


def _tokens_compatible(requested: str, observed: str) -> bool:
    requested_tokens = set(requested.split())
    observed_tokens = set(observed.split())
    return bool(requested_tokens) and (
        requested_tokens <= observed_tokens
        or observed_tokens <= requested_tokens
        or requested.replace(" ", "") == observed.replace(" ", "")
    )


def _structured_sort(
    records: Sequence[MusicSearchRecord],
    *,
    sort_by: str,
    sort_order: str,
) -> list[MusicSearchRecord]:
    if sort_by == "release_date":
        metadata_key = "release_date_epoch"
    elif sort_by == "popularity":
        metadata_key = "popularity_score"
    else:
        raise ValueError(f"Unsupported music sort field: {sort_by}")
    if sort_order not in {"asc", "desc"}:
        raise ValueError(f"Unsupported music sort order: {sort_order}")
    return sorted(
        records,
        key=lambda record: (
            float(record.metadata.get(metadata_key, 0)),
            record.id,
        ),
        reverse=sort_order == "desc",
    )


def _exact_match_boost(
    record: MusicSearchRecord,
    extraction: Mapping[str, Any],
) -> float:
    boost = 0.0
    requested_title = normalize_music_text(_optional_text(extraction.get("title")))
    observed_title = normalize_music_text(str(record.metadata.get("normalized_title", "")))
    if requested_title and requested_title == observed_title:
        boost += 0.05
    elif requested_title and _tokens_compatible(requested_title, observed_title):
        boost += 0.025
    requested_artist = normalize_music_text(_optional_text(extraction.get("artist")))
    artists = [
        normalize_music_text(str(value))
        for value in record.metadata.get("artist_keys", [])
    ]
    if requested_artist and requested_artist in artists:
        boost += 0.02
    elif requested_artist and any(
        _tokens_compatible(requested_artist, artist) for artist in artists
    ):
        boost += 0.01
    return boost


def _candidate_payload(
    record: MusicSearchRecord,
    *,
    final_score: float | None,
    dense_rank: int | None = None,
    bm25_rank: int | None = None,
    rrf_score: float | None = None,
    exact_boost: float | None = None,
) -> dict[str, Any]:
    metadata = record.metadata
    explicit_aliases = metadata.get("title_aliases", [])
    return {
        "record_id": record.id,
        "track_id": metadata.get("track_id"),
        "title": metadata.get("title"),
        "title_aliases": list(
            music_title_aliases(
                str(metadata.get("title", "")),
                explicit_aliases if isinstance(explicit_aliases, list) else None,
            )
        ),
        "artists": list(metadata.get("artist_names", [])),
        "video_id": metadata.get("video_id"),
        "content_type": metadata.get("content_type"),
        "version": metadata.get("version"),
        "thumbnail_url": metadata.get("thumbnail_url"),
        "duration_seconds": metadata.get("duration_seconds"),
        "release_date": metadata.get("release_date"),
        "release_date_origin": metadata.get("release_date_origin"),
        "view_count": metadata.get("view_count"),
        "ranking": {
            "dense_rank": dense_rank,
            "bm25_rank": bm25_rank,
            "rrf_score": rrf_score,
            "exact_boost": exact_boost,
            "final_score": final_score,
        },
    }


def _optional_text(raw: Any) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def _normalize_language(raw: str) -> str:
    normalized = normalize_music_text(raw)
    return LANGUAGE_ALIASES.get(normalized, normalized)
