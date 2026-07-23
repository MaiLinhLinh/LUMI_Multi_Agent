"""Validate a curated music catalog, embed it with Ollama and upsert ChromaDB."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from rag_manager.config import Settings, load_settings
from rag_manager.services.music_embedding_service import (
    MusicEmbeddingError,
    OllamaMusicEmbeddingService,
)
from rag_manager.services.music_repository import (
    MusicChromaRecord,
    MusicChromaRepository,
)
from rag_manager.services.music_youtube_api import (
    YouTubeMetadataError,
    YouTubeMetadataService,
)


MUSIC_SOURCE_SCHEMA_VERSION = "music.chroma-source.v1"
MUSIC_CATALOG_VERSION = "music.catalog.v1"
EMBEDDING_VERSION = 1
YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
SAFE_THUMBNAIL_HOSTS = {"i.ytimg.com", "img.youtube.com"}
CONTENT_TYPES = {
    "official_mv",
    "official_audio",
    "lyric_video",
    "live",
    "remix",
    "acoustic",
    "karaoke",
    "performance",
    "teaser",
    "other",
}
VERSION_LABELS = {
    "official_mv": "official MV",
    "official_audio": "official audio",
    "lyric_video": "lyric video",
    "live": "live",
    "remix": "remix",
    "acoustic": "acoustic",
    "karaoke": "karaoke",
    "performance": "performance",
    "teaser": "teaser",
    "other": "other",
}


class MusicCatalogError(ValueError):
    """One catalog field is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class PreparedMusicRecord:
    id: str
    document: str
    metadata: dict[str, Any]


def load_music_catalog(path: str | Path) -> list[PreparedMusicRecord]:
    """Read and strictly normalize a track-centric JSON catalog."""

    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MusicCatalogError(f"Music catalog file not found: {source}") from exc
    except json.JSONDecodeError as exc:
        raise MusicCatalogError(
            f"Music catalog is invalid JSON at line {exc.lineno}: {exc.msg}"
        ) from exc

    if isinstance(payload, dict):
        version = str(payload.get("catalog_version", "")).strip()
        if version and version != MUSIC_CATALOG_VERSION:
            raise MusicCatalogError(
                f"Unsupported catalog_version {version!r}; expected {MUSIC_CATALOG_VERSION!r}"
            )
        tracks = payload.get("tracks")
    else:
        tracks = payload
    if not isinstance(tracks, list) or not tracks:
        raise MusicCatalogError("Music catalog must contain a non-empty 'tracks' list")

    now = _utc_now_text()
    prepared: list[PreparedMusicRecord] = []
    seen_ids: set[str] = set()
    seen_sources: set[str] = set()
    for track_index, raw_track in enumerate(tracks, start=1):
        if not isinstance(raw_track, dict):
            raise MusicCatalogError(f"tracks[{track_index}] must be an object")
        records = _prepare_track(raw_track, track_index=track_index, now=now)
        for record in records:
            if record.id in seen_ids:
                raise MusicCatalogError(
                    f"Duplicate YouTube source/record id in catalog: {record.id!r}"
                )
            seen_ids.add(record.id)
            source_key = f"{record.metadata['platform']}:{record.metadata['video_id']}"
            if source_key in seen_sources:
                raise MusicCatalogError(
                    f"Duplicate playable source in catalog: {source_key!r}"
                )
            seen_sources.add(source_key)
            prepared.append(record)
    return prepared


def import_music_catalog(
    catalog_path: str | Path,
    *,
    settings: Settings | None = None,
    repository: MusicChromaRepository | None = None,
    embedding_service: OllamaMusicEmbeddingService | None = None,
    youtube_service: YouTubeMetadataService | None = None,
    verify_youtube: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate, incrementally embed and idempotently upsert one catalog."""

    settings = settings or load_settings()
    started = time.perf_counter()
    prepared = load_music_catalog(catalog_path)
    prepared = [
        _with_embedding_config(
            record,
            model=settings.music_embedding_model,
        )
        for record in prepared
    ]
    if verify_youtube:
        owns_youtube_service = youtube_service is None
        youtube_service = youtube_service or YouTubeMetadataService(
            api_key=settings.youtube_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
        try:
            video_metadata = youtube_service.fetch_videos(
                record.metadata["video_id"] for record in prepared
            )
        finally:
            if owns_youtube_service:
                youtube_service.close()
        prepared = [
            _apply_youtube_metadata(record, video_metadata) for record in prepared
        ]
        unavailable = sum(
            not bool(record.metadata["source_active"]) for record in prepared
        )
        _log(
            "YOUTUBE_VERIFIED",
            requested=len(prepared),
            found=len(video_metadata),
            inactive=unavailable,
        )
    _log("VALIDATED", records=len(prepared), catalog=str(catalog_path))
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "catalog": str(catalog_path),
            "validated_records": len(prepared),
            "elapsed_seconds": time.perf_counter() - started,
        }

    repository = repository or MusicChromaRepository(
        path=settings.music_chroma_path,
        collection_name=settings.music_chroma_collection,
        embedding_dimensions=settings.music_embedding_dimensions,
    )
    existing = repository.get_existing(record.id for record in prepared)
    prepared = [_preserve_created_at(record, existing.get(record.id)) for record in prepared]

    records: list[MusicChromaRecord | None] = [None] * len(prepared)
    embed_indexes: list[int] = []
    embed_texts: list[str] = []
    reused = 0
    for index, record in enumerate(prepared):
        old = existing.get(record.id)
        if _can_reuse_embedding(
            old,
            record,
            model=settings.music_embedding_model,
            dimensions=settings.music_embedding_dimensions,
        ):
            records[index] = MusicChromaRecord(
                id=record.id,
                document=record.document,
                embedding=old.embedding,
                metadata=record.metadata,
            )
            reused += 1
        else:
            embed_indexes.append(index)
            embed_texts.append(record.document)

    owns_embedding_service = embedding_service is None
    embedding_service = embedding_service or OllamaMusicEmbeddingService(
        base_url=settings.ollama_base_url,
        model=settings.music_embedding_model,
        dimensions=settings.music_embedding_dimensions,
        timeout_seconds=settings.music_embedding_timeout_seconds,
    )
    try:
        if embed_texts:
            _log(
                "EMBED_START",
                records=len(embed_texts),
                model=settings.music_embedding_model,
                batch_size=settings.music_embedding_batch_size,
            )
            embed_started = time.perf_counter()
            vectors = embedding_service.embed_documents(
                embed_texts,
                batch_size=settings.music_embedding_batch_size,
            )
            _log(
                "EMBED_DONE",
                records=len(vectors),
                elapsed_seconds=time.perf_counter() - embed_started,
            )
            for index, vector in zip(embed_indexes, vectors, strict=True):
                prepared_record = prepared[index]
                records[index] = MusicChromaRecord(
                    id=prepared_record.id,
                    document=prepared_record.document,
                    embedding=vector,
                    metadata=prepared_record.metadata,
                )
    finally:
        if owns_embedding_service:
            embedding_service.close()

    complete_records = [record for record in records if record is not None]
    if len(complete_records) != len(prepared):
        raise RuntimeError("Internal error: not every music record received an embedding")
    _log("UPSERT_START", records=len(complete_records))
    upserted = repository.upsert(complete_records)
    count = repository.count()
    elapsed = time.perf_counter() - started
    _log("COMPLETED", upserted=upserted, collection_count=count, elapsed_seconds=elapsed)
    return {
        "ok": True,
        "dry_run": False,
        "catalog": str(catalog_path),
        "validated_records": len(prepared),
        "embedded_records": len(embed_texts),
        "reused_embeddings": reused,
        "upserted_records": upserted,
        "collection_count": count,
        "chroma_path": str(settings.music_chroma_path),
        "collection": settings.music_chroma_collection,
        "embedding_model": settings.music_embedding_model,
        "embedding_dimensions": settings.music_embedding_dimensions,
        "elapsed_seconds": elapsed,
    }


def _prepare_track(
    raw: dict[str, Any],
    *,
    track_index: int,
    now: str,
) -> list[PreparedMusicRecord]:
    prefix = f"tracks[{track_index}]"
    title = _required_text(raw.get("title"), f"{prefix}.title")
    artists = _required_string_list(raw.get("artists"), f"{prefix}.artists")
    artist_keys = [_normalize_key(value) for value in artists]
    normalized_title = _normalize_key(title)
    canonical_key = str(raw.get("canonical_key", "")).strip() or (
        f"{artist_keys[0]}::{normalized_title}"
    )
    track_id = str(raw.get("track_id", "")).strip() or _stable_id(
        "track", canonical_key
    )
    _validate_backend_id(track_id, f"{prefix}.track_id")

    release_date, release_precision, release_epoch = _release_date_fields(
        raw.get("release_date"),
        raw.get("release_date_precision"),
        field=f"{prefix}.release_date",
    )
    genres = _optional_string_list(raw.get("genres"), f"{prefix}.genres")
    moods = _optional_string_list(raw.get("moods"), f"{prefix}.moods")
    tags = _optional_string_list(raw.get("tags"), f"{prefix}.tags")
    language = _required_text(raw.get("language"), f"{prefix}.language")
    popularity = _number(raw.get("popularity_score", 0), f"{prefix}.popularity_score")
    if popularity < 0:
        raise MusicCatalogError(f"{prefix}.popularity_score must not be negative")
    track_active = _boolean(raw.get("track_active", True), f"{prefix}.track_active")
    review_required = _boolean(
        raw.get("review_required", False), f"{prefix}.review_required"
    )
    release_date_origin = str(raw.get("release_date_origin", "curated")).strip()
    if release_date_origin not in {"curated", "youtube_published_at_proxy"}:
        raise MusicCatalogError(
            f"{prefix}.release_date_origin must be curated or youtube_published_at_proxy"
        )
    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        raise MusicCatalogError(f"{prefix}.sources must be a non-empty list")

    records: list[PreparedMusicRecord] = []
    for source_index, source in enumerate(sources, start=1):
        source_field = f"{prefix}.sources[{source_index}]"
        if not isinstance(source, dict):
            raise MusicCatalogError(f"{source_field} must be an object")
        platform = str(source.get("platform", "youtube")).strip().lower()
        if platform != "youtube":
            raise MusicCatalogError(f"{source_field}.platform must be 'youtube'")
        video_id = _required_text(source.get("video_id"), f"{source_field}.video_id")
        if not YOUTUBE_VIDEO_ID_PATTERN.fullmatch(video_id):
            raise MusicCatalogError(
                f"{source_field}.video_id must be an 11-character YouTube video id"
            )
        content_type = _required_text(
            source.get("content_type"), f"{source_field}.content_type"
        ).lower()
        if content_type not in CONTENT_TYPES:
            raise MusicCatalogError(
                f"{source_field}.content_type must be one of {sorted(CONTENT_TYPES)}"
            )
        version = str(source.get("version", "")).strip() or VERSION_LABELS[content_type]
        published_at, published_epoch = _published_at_fields(
            source.get("published_at"), field=f"{source_field}.published_at"
        )
        thumbnail_url = _thumbnail_url(
            source.get("thumbnail_url", ""), field=f"{source_field}.thumbnail_url"
        )
        duration_seconds = _integer(
            source.get("duration_seconds"), f"{source_field}.duration_seconds"
        )
        if duration_seconds <= 0:
            raise MusicCatalogError(f"{source_field}.duration_seconds must be positive")
        is_official = _boolean(
            source.get("is_official"), f"{source_field}.is_official"
        )
        embeddable = _boolean(
            source.get("embeddable"), f"{source_field}.embeddable"
        )
        source_active = _boolean(
            source.get("source_active", True), f"{source_field}.source_active"
        )
        source_id = str(source.get("source_id", "")).strip() or f"youtube_{video_id}"
        _validate_backend_id(source_id, f"{source_field}.source_id")

        metadata: dict[str, Any] = {
            "schema_version": MUSIC_SOURCE_SCHEMA_VERSION,
            "track_id": track_id,
            "canonical_key": canonical_key,
            "title": title,
            "normalized_title": normalized_title,
            "artist_names": artists,
            "artist_keys": artist_keys,
            "language": language,
            "release_date": release_date,
            "release_date_epoch": release_epoch,
            "release_date_precision": release_precision,
            "release_date_origin": release_date_origin,
            "popularity_score": popularity,
            "platform": platform,
            "video_id": video_id,
            "content_type": content_type,
            "version": version,
            "channel_id": _required_text(
                source.get("channel_id"), f"{source_field}.channel_id"
            ),
            "channel_name": _required_text(
                source.get("channel_name"), f"{source_field}.channel_name"
            ),
            "is_official": is_official,
            "embeddable": embeddable,
            "published_at": published_at,
            "published_at_epoch": published_epoch,
            "thumbnail_url": thumbnail_url,
            "duration_seconds": duration_seconds,
            "track_active": track_active,
            "review_required": review_required,
            "source_active": source_active,
            "embedding_model": "bge-m3",
            "embedding_version": EMBEDDING_VERSION,
            "created_at": now,
            "updated_at": now,
        }
        for key in ("view_count", "like_count"):
            raw_count = source.get(key, 0)
            count = _integer(raw_count, f"{source_field}.{key}")
            if count < 0:
                raise MusicCatalogError(f"{source_field}.{key} must not be negative")
            metadata[key] = count
        for key, values in (("genres", genres), ("moods", moods), ("tags", tags)):
            if values:
                metadata[key] = values
        document = _build_document(
            title=title,
            normalized_title=normalized_title,
            artists=artists,
            artist_keys=artist_keys,
            genres=genres,
            moods=moods,
            tags=tags,
            language=language,
            version=version,
            content_type=content_type,
        )
        records.append(
            PreparedMusicRecord(id=source_id, document=document, metadata=metadata)
        )
    return records


def _build_document(**fields: Any) -> str:
    values: list[str] = []
    for value in fields.values():
        if isinstance(value, list):
            values.extend(str(item).strip() for item in value)
        else:
            values.append(str(value).replace("_", " ").strip())
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            unique.append(value)
    return " | ".join(unique)


def _can_reuse_embedding(
    existing: MusicChromaRecord | None,
    prepared: PreparedMusicRecord,
    *,
    model: str,
    dimensions: int,
) -> bool:
    return bool(
        existing
        and existing.document == prepared.document
        and existing.metadata.get("embedding_model") == model
        and existing.metadata.get("embedding_version") == EMBEDDING_VERSION
        and len(existing.embedding) == dimensions
    )


def _preserve_created_at(
    record: PreparedMusicRecord,
    existing: MusicChromaRecord | None,
) -> PreparedMusicRecord:
    if existing is None:
        return record
    created_at = existing.metadata.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        return record
    metadata = dict(record.metadata)
    metadata["created_at"] = created_at
    return replace(record, metadata=metadata)


def _with_embedding_config(
    record: PreparedMusicRecord,
    *,
    model: str,
) -> PreparedMusicRecord:
    metadata = dict(record.metadata)
    metadata["embedding_model"] = model
    metadata["embedding_version"] = EMBEDDING_VERSION
    return replace(record, metadata=metadata)


def _apply_youtube_metadata(
    record: PreparedMusicRecord,
    video_metadata: dict[str, dict[str, Any]],
) -> PreparedMusicRecord:
    metadata = dict(record.metadata)
    video_id = str(metadata["video_id"])
    observed = video_metadata.get(video_id)
    if observed is None:
        metadata["embeddable"] = False
        metadata["source_active"] = False
        metadata["youtube_status"] = "unavailable"
        return replace(record, metadata=metadata)

    for key in ("channel_id", "channel_name"):
        value = observed.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()
    published_at = observed.get("published_at")
    if isinstance(published_at, str) and published_at.strip():
        normalized, epoch = _published_at_fields(
            published_at,
            field=f"YouTube video {video_id}.published_at",
        )
        metadata["published_at"] = normalized
        metadata["published_at_epoch"] = epoch
    thumbnail = observed.get("thumbnail_url")
    if isinstance(thumbnail, str) and thumbnail.strip():
        metadata["thumbnail_url"] = _thumbnail_url(
            thumbnail,
            field=f"YouTube video {video_id}.thumbnail_url",
        )
    duration = observed.get("duration_seconds")
    if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
        metadata["duration_seconds"] = duration
    metadata["embeddable"] = observed.get("embeddable") is True
    metadata["source_active"] = bool(metadata["source_active"]) and (
        observed.get("source_active") is True
    )
    metadata["youtube_status"] = (
        "available" if metadata["source_active"] else "inactive"
    )
    return replace(record, metadata=metadata)


def _release_date_fields(
    raw_value: Any,
    raw_precision: Any,
    *,
    field: str,
) -> tuple[str, str, int]:
    value = _required_text(raw_value, field)
    inferred = {4: "year", 7: "month", 10: "day"}.get(len(value))
    precision = str(raw_precision or inferred or "").strip().lower()
    if precision not in {"year", "month", "day"}:
        raise MusicCatalogError(f"{field}_precision must be year, month, or day")
    patterns = {
        "year": r"^\d{4}$",
        "month": r"^\d{4}-\d{2}$",
        "day": r"^\d{4}-\d{2}-\d{2}$",
    }
    if not re.fullmatch(patterns[precision], value):
        raise MusicCatalogError(f"{field} does not match precision {precision!r}")
    normalized = value + ("-01-01" if precision == "year" else "-01" if precision == "month" else "")
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise MusicCatalogError(f"{field} is not a valid calendar date") from exc
    return normalized, precision, int(parsed.timestamp())


def _published_at_fields(raw_value: Any, *, field: str) -> tuple[str, int]:
    value = _required_text(raw_value, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MusicCatalogError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise MusicCatalogError(f"{field} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    return utc.isoformat().replace("+00:00", "Z"), int(utc.timestamp())


def _thumbnail_url(raw_value: Any, *, field: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in SAFE_THUMBNAIL_HOSTS:
        raise MusicCatalogError(
            f"{field} must use HTTPS on an approved YouTube thumbnail host"
        )
    return value


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MusicCatalogError(f"{field} must be a non-empty string")
    return text


def _required_string_list(value: Any, field: str) -> list[str]:
    values = _optional_string_list(value, field)
    if not values:
        raise MusicCatalogError(f"{field} must contain at least one string")
    return values


def _optional_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MusicCatalogError(f"{field} must be a list of strings")
    values: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise MusicCatalogError(f"{field} must contain only non-empty strings")
        text = item.strip()
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            values.append(text)
    return values


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise MusicCatalogError(f"{field} must be true or false")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MusicCatalogError(f"{field} must be an integer")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MusicCatalogError(f"{field} must be numeric")
    return float(value)


def _normalize_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.replace("Đ", "D").replace("đ", "d"))
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_marks.casefold()).strip()


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _validate_backend_id(value: str, field: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}", value):
        raise MusicCatalogError(f"{field} contains unsupported characters")


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _log(event: str, **fields: Any) -> None:
    payload = " ".join(
        f"{key}={value:.3f}" if isinstance(value, float) else f"{key}={value!r}"
        for key, value in fields.items()
    )
    print(f"[MUSIC_CATALOG][{event}] {payload}".rstrip(), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-file",
        help="Curated JSON catalog; defaults to MUSIC_CATALOG_FILE.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and normalize metadata without Ollama or ChromaDB.",
    )
    parser.add_argument(
        "--verify-youtube",
        action="store_true",
        help="Refresh source facts/status with YouTube Data API before upsert.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    settings = load_settings()
    catalog_path = str(args.input_file or settings.music_catalog_file).strip()
    if not catalog_path:
        parser.error("--input-file or MUSIC_CATALOG_FILE is required")
    try:
        result = import_music_catalog(
            catalog_path,
            settings=settings,
            verify_youtube=bool(args.verify_youtube),
            dry_run=bool(args.dry_run),
        )
    except (
        MusicCatalogError,
        MusicEmbeddingError,
        YouTubeMetadataError,
        RuntimeError,
        ValueError,
    ) as exc:
        error = {
            "ok": False,
            "error": {
                "source": "music_catalog_worker",
                "code": getattr(exc, "code", "music_catalog_import_failed"),
                "message": str(exc),
                "retryable": bool(getattr(exc, "retryable", False)),
                "exception_type": exc.__class__.__name__,
            },
        }
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
