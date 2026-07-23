"""Build a one-video-per-track catalog from confirmed official YouTube channels."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from rag_manager.config import Settings, load_settings
from rag_manager.services.music_catalog_worker import MUSIC_CATALOG_VERSION
from rag_manager.services.music_youtube_api import (
    YouTubeMetadataError,
    YouTubeMetadataService,
)


CHANNELS_VERSION = "music.youtube-channels.v1"
REVIEW_VERSION = "music.youtube-collection-review.v1"
CHANNEL_ID_PATTERN = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
NON_SONG_PHRASES = {
    "behind the scenes",
    "making of",
    "reaction",
    "teaser",
    "trailer",
    "preview",
    "interview",
    "phong van",
    "hau truong",
    "vlog",
    "documentary",
    "dance practice",
    "challenge",
    "livestream",
    "karaoke",
    "instrumental",
    "shorts",
}
VERSION_PHRASES = {
    "official music video",
    "official video",
    "official mv",
    "music video",
    "official audio",
    "audio official",
    "lyric video",
    "lyrics video",
    "official lyric",
    "visualizer",
    "performance video",
    "live performance",
    "live",
    "remix",
    "acoustic",
    "official",
    "mv",
    "audio",
    "lyrics",
    "lyric",
    "video",
    "4k",
    "hd",
}
VERSION_SUBSTRINGS = {
    "official music video",
    "official video",
    "official mv",
    "music video",
    "official audio",
    "audio official",
    "lyric video",
    "lyrics video",
    "official lyric",
    "visualizer",
    "performance video",
    "live performance",
}
CONTENT_PRIORITY = {
    "official_mv": 600,
    "official_audio": 500,
    "lyric_video": 400,
    "performance": 300,
    "acoustic": 250,
    "live": 200,
    "remix": 150,
    "other": 100,
}
VERSION_LABELS = {
    "official_mv": "official MV",
    "official_audio": "official audio",
    "lyric_video": "lyric video",
    "performance": "performance",
    "acoustic": "acoustic",
    "live": "live",
    "remix": "remix",
    "other": "official channel upload",
}


class MusicYouTubeCollectorError(ValueError):
    """A confirmed-channel configuration or generated result is unsafe."""


@dataclass(frozen=True)
class OfficialChannel:
    artist: str
    channel_id: str
    language: str
    genres: tuple[str, ...] = ()
    moods: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    min_duration_seconds: int = 90


@dataclass(frozen=True)
class VideoCandidate:
    artist: str
    canonical_title: str
    canonical_key: str
    content_type: str
    video: dict[str, Any]
    review_required: bool

    @property
    def rank(self) -> tuple[int, int, int, str]:
        title_key = _normalize_key(str(self.video.get("youtube_title", "")))
        official_marker = 1 if "official" in title_key else 0
        return (
            CONTENT_PRIORITY[self.content_type],
            official_marker,
            int(self.video.get("view_count", 0)),
            str(self.video.get("published_at", "")),
        )


def load_official_channels(path: str | Path) -> list[OfficialChannel]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MusicYouTubeCollectorError(
            f"Official music channels file not found: {source}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MusicYouTubeCollectorError(
            f"Official music channels JSON is invalid at line {exc.lineno}: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("channels_version") != CHANNELS_VERSION:
        raise MusicYouTubeCollectorError(
            f"channels_version must be {CHANNELS_VERSION!r}"
        )
    raw_channels = payload.get("channels")
    if not isinstance(raw_channels, list) or not raw_channels:
        raise MusicYouTubeCollectorError("channels must be a non-empty list")

    channels: list[OfficialChannel] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_channels, start=1):
        field = f"channels[{index}]"
        if not isinstance(raw, dict):
            raise MusicYouTubeCollectorError(f"{field} must be an object")
        if raw.get("active", True) is False:
            continue
        if raw.get("confirmed_official") is not True:
            raise MusicYouTubeCollectorError(
                f"{field}.confirmed_official must be true after you manually "
                "verify the channel on YouTube"
            )
        artist = _required_text(raw.get("artist"), f"{field}.artist")
        channel_id = _required_text(raw.get("channel_id"), f"{field}.channel_id")
        if not CHANNEL_ID_PATTERN.fullmatch(channel_id):
            raise MusicYouTubeCollectorError(
                f"{field}.channel_id must be a canonical 24-character YouTube channel id"
            )
        if channel_id in seen_ids:
            raise MusicYouTubeCollectorError(f"Duplicate channel_id: {channel_id}")
        seen_ids.add(channel_id)
        language = _required_text(raw.get("language"), f"{field}.language")
        min_duration = raw.get("min_duration_seconds", 90)
        if isinstance(min_duration, bool) or not isinstance(min_duration, int):
            raise MusicYouTubeCollectorError(
                f"{field}.min_duration_seconds must be an integer"
            )
        if min_duration < 30:
            raise MusicYouTubeCollectorError(
                f"{field}.min_duration_seconds must be at least 30"
            )
        channels.append(
            OfficialChannel(
                artist=artist,
                channel_id=channel_id,
                language=language,
                genres=tuple(_string_list(raw.get("genres"), f"{field}.genres")),
                moods=tuple(_string_list(raw.get("moods"), f"{field}.moods")),
                tags=tuple(_string_list(raw.get("tags"), f"{field}.tags")),
                min_duration_seconds=min_duration,
            )
        )
    if not channels:
        raise MusicYouTubeCollectorError("No active confirmed official channels found")
    return channels


def collect_music_catalog(
    channels: Iterable[OfficialChannel],
    *,
    youtube_service: YouTubeMetadataService,
    max_videos_per_channel: int | None = None,
    max_tracks_per_channel: int = 10,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect, filter, group and choose one playable source per canonical song."""

    if max_tracks_per_channel <= 0:
        raise MusicYouTubeCollectorError("max_tracks_per_channel must be positive")
    channel_list = list(channels)
    resolved = youtube_service.fetch_upload_playlists(
        channel.channel_id for channel in channel_list
    )
    generated_at = _utc_now_text()
    tracks: list[dict[str, Any]] = []
    channel_reports: list[dict[str, Any]] = []

    for channel in channel_list:
        remote_channel = resolved.get(channel.channel_id)
        if remote_channel is None:
            raise MusicYouTubeCollectorError(
                f"YouTube did not return confirmed channel {channel.channel_id!r}"
            )
        uploads_id = remote_channel["uploads_playlist_id"]
        video_ids = youtube_service.fetch_upload_video_ids(
            uploads_id,
            max_videos=max_videos_per_channel,
        )
        videos = youtube_service.fetch_videos(video_ids)
        selected, report = _select_channel_tracks(
            channel,
            remote_channel_name=remote_channel.get("channel_name", ""),
            uploads_playlist_id=uploads_id,
            requested_video_ids=video_ids,
            videos=videos,
            max_tracks=max_tracks_per_channel,
        )
        tracks.extend(selected)
        channel_reports.append(report)

    if not tracks:
        raise MusicYouTubeCollectorError(
            "No public, embeddable music videos passed the collection policy"
        )
    catalog = {
        "catalog_version": MUSIC_CATALOG_VERSION,
        "generated_at": generated_at,
        "source": "confirmed_official_youtube_channels",
        "one_video_per_track": True,
        "tracks": tracks,
    }
    review = {
        "review_version": REVIEW_VERSION,
        "generated_at": generated_at,
        "selection_policy": {
            "source": "confirmed_channel_uploads_playlist",
            "content_priority": list(CONTENT_PRIORITY),
            "tie_breakers": ["official_marker", "view_count", "published_at"],
            "release_date_origin": "youtube_published_at_proxy",
            "track_ranking": "selected_video_view_count_desc",
            "max_tracks_per_channel": max_tracks_per_channel,
        },
        "channel_reports": channel_reports,
        "selected_track_count": len(tracks),
        "review_required_count": sum(
            bool(track.get("review_required")) for track in tracks
        ),
    }
    return catalog, review


def write_collection_outputs(
    *,
    catalog: dict[str, Any],
    review: dict[str, Any],
    catalog_path: str | Path,
    review_path: str | Path,
    force: bool = False,
) -> None:
    catalog_target = Path(catalog_path)
    review_target = Path(review_path)
    if catalog_target.resolve() == review_target.resolve():
        raise MusicYouTubeCollectorError(
            "Catalog output and review output must use different paths"
        )
    for target in (catalog_target, review_target):
        if target.exists() and not force:
            raise MusicYouTubeCollectorError(
                f"Refusing to overwrite {target}; pass --force after reviewing the path"
            )
    catalog_target.parent.mkdir(parents=True, exist_ok=True)
    review_target.parent.mkdir(parents=True, exist_ok=True)
    catalog_target.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    review_target.write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _select_channel_tracks(
    channel: OfficialChannel,
    *,
    remote_channel_name: str,
    uploads_playlist_id: str,
    requested_video_ids: list[str],
    videos: dict[str, dict[str, Any]],
    max_tracks: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    groups: dict[str, list[VideoCandidate]] = {}
    rejected: list[dict[str, str]] = []
    for video_id in requested_video_ids:
        video = videos.get(video_id)
        if video is None:
            rejected.append({"video_id": video_id, "reason": "unavailable_or_private"})
            continue
        reason = _rejection_reason(video, channel)
        if reason:
            rejected.append(
                {
                    "video_id": video_id,
                    "title": str(video.get("youtube_title", "")),
                    "reason": reason,
                }
            )
            continue
        title = str(video.get("youtube_title", "")).strip()
        content_type = classify_content_type(title)
        canonical_title = canonicalize_title(title, artist=channel.artist)
        canonical_key = _normalize_key(canonical_title)
        if len(canonical_key) < 2:
            rejected.append(
                {"video_id": video_id, "title": title, "reason": "ambiguous_title"}
            )
            continue
        candidate = VideoCandidate(
            artist=channel.artist,
            canonical_title=canonical_title,
            canonical_key=canonical_key,
            content_type=content_type,
            video=video,
            review_required=content_type == "other",
        )
        groups.setdefault(canonical_key, []).append(candidate)

    ranked_groups: list[tuple[VideoCandidate, list[VideoCandidate]]] = []
    for _canonical_key, candidates in sorted(groups.items()):
        ordered = sorted(candidates, key=lambda item: item.rank, reverse=True)
        chosen = ordered[0]
        ranked_groups.append((chosen, ordered))

    ranked_groups.sort(
        key=lambda item: (
            int(item[0].video.get("view_count", 0)),
            str(item[0].video.get("published_at", "")),
            item[0].canonical_key,
        ),
        reverse=True,
    )
    included = ranked_groups[:max_tracks]
    omitted = ranked_groups[max_tracks:]
    tracks: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    for chosen, ordered in included:
        tracks.append(_candidate_to_track(chosen, channel))
        selections.append(
            {
                "artist": channel.artist,
                "canonical_title": chosen.canonical_title,
                "canonical_key": chosen.canonical_key,
                "selected_video_id": chosen.video["video_id"],
                "selected_content_type": chosen.content_type,
                "selected_view_count": chosen.video.get("view_count", 0),
                "review_required": chosen.review_required,
                "alternatives": [
                    {
                        "video_id": item.video["video_id"],
                        "title": item.video.get("youtube_title", ""),
                        "content_type": item.content_type,
                        "view_count": item.video.get("view_count", 0),
                    }
                    for item in ordered[1:]
                ],
            }
        )
    report = {
        "artist": channel.artist,
        "configured_channel_id": channel.channel_id,
        "youtube_channel_name": remote_channel_name,
        "uploads_playlist_id": uploads_playlist_id,
        "upload_items_seen": len(requested_video_ids),
        "video_details_found": len(videos),
        "selected_tracks": len(tracks),
        "track_limit": max_tracks,
        "omitted_by_track_limit": [
            {
                "canonical_title": chosen.canonical_title,
                "selected_video_id": chosen.video["video_id"],
                "selected_view_count": chosen.video.get("view_count", 0),
            }
            for chosen, _ordered in omitted
        ],
        "selections": selections,
        "rejected": rejected,
    }
    return tracks, report


def _candidate_to_track(
    candidate: VideoCandidate,
    channel: OfficialChannel,
) -> dict[str, Any]:
    video = candidate.video
    published_at = str(video["published_at"])
    youtube_tags = video.get("youtube_tags")
    tags = _unique_strings(
        [*channel.tags, *(youtube_tags if isinstance(youtube_tags, list) else [])]
    )[:20]
    return {
        "title": candidate.canonical_title,
        "artists": [channel.artist],
        "genres": list(channel.genres),
        "moods": list(channel.moods),
        "language": channel.language,
        "tags": tags,
        "release_date": published_at[:10],
        "release_date_precision": "day",
        "release_date_origin": "youtube_published_at_proxy",
        "popularity_score": float(video.get("view_count", 0)),
        "track_active": True,
        "review_required": candidate.review_required,
        "sources": [
            {
                "platform": "youtube",
                "video_id": video["video_id"],
                "content_type": candidate.content_type,
                "version": VERSION_LABELS[candidate.content_type],
                "channel_id": channel.channel_id,
                "channel_name": video.get("channel_name", ""),
                "is_official": True,
                "embeddable": True,
                "published_at": published_at,
                "thumbnail_url": video.get("thumbnail_url", ""),
                "duration_seconds": int(video["duration_seconds"]),
                "view_count": int(video.get("view_count", 0)),
                "like_count": int(video.get("like_count", 0)),
                "source_active": True,
            }
        ],
    }


def _rejection_reason(video: dict[str, Any], channel: OfficialChannel) -> str:
    if video.get("channel_id") != channel.channel_id:
        return "channel_id_mismatch"
    if video.get("source_active") is not True:
        return "not_public_or_not_processed"
    if video.get("embeddable") is not True:
        return "embedding_disabled"
    duration = video.get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, int):
        return "invalid_duration"
    if duration < channel.min_duration_seconds:
        return "below_minimum_duration"
    title_key = _normalize_key(str(video.get("youtube_title", "")))
    if any(phrase in title_key for phrase in NON_SONG_PHRASES):
        return "non_song_title"
    if not str(video.get("published_at", "")).strip():
        return "missing_published_at"
    return ""


def classify_content_type(title: str) -> str:
    key = _normalize_key(title)
    if any(phrase in key for phrase in ("official music video", "official mv", "music video")):
        return "official_mv"
    if "official audio" in key or "audio official" in key:
        return "official_audio"
    if any(phrase in key for phrase in ("lyric video", "lyrics video", "official lyric")):
        return "lyric_video"
    if "acoustic" in key:
        return "acoustic"
    if "remix" in key:
        return "remix"
    if "performance" in key:
        return "performance"
    if "live" in key:
        return "live"
    return "other"


def canonicalize_title(title: str, *, artist: str) -> str:
    """Remove only known source/version decorations; preserve uncertain words."""

    value = re.sub(r"\s+", " ", title).strip()
    value = re.sub(
        r"[\(\[\{]([^\)\]\}]+)[\)\]\}]",
        lambda match: "" if _contains_version_phrase(match.group(1)) else match.group(0),
        value,
    )
    parts = re.split(r"\s*(?:\||｜|•)\s*", value)
    artist_key = _normalize_key(artist)
    useful = [
        part.strip()
        for part in parts
        if part.strip()
        and _normalize_key(part) != artist_key
        and not _contains_version_phrase(part)
    ]
    value = " | ".join(useful) if useful else value
    prefix_pattern = re.compile(
        rf"^{re.escape(artist)}\s*(?:-|–|—|:)+\s*",
        flags=re.IGNORECASE,
    )
    value = prefix_pattern.sub("", value).strip()
    value = re.sub(
        r"\s*(?:-|–|—|:)\s*(?:official(?:\s+music)?\s+video|"
        r"official\s+mv|official\s+audio|lyric\s+video|visualizer|"
        r"performance\s+video|live|remix|acoustic)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip(" \t-–—|:[](){}'\"")


def _contains_version_phrase(value: str) -> bool:
    key = _normalize_key(value)
    return key in VERSION_PHRASES or any(phrase in key for phrase in VERSION_SUBSTRINGS)


def _normalize_key(value: str) -> str:
    decomposed = unicodedata.normalize(
        "NFKD", value.replace("Đ", "D").replace("đ", "d")
    )
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", without_marks.casefold()).strip()


def _required_text(raw: Any, field: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise MusicYouTubeCollectorError(f"{field} must be a non-empty string")
    return value


def _string_list(raw: Any, field: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw
    ):
        raise MusicYouTubeCollectorError(f"{field} must be a list of strings")
    return _unique_strings(raw)


def _unique_strings(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channels-file", required=True)
    parser.add_argument("--catalog-out", default="data/music_catalog.generated.json")
    parser.add_argument("--review-out", default="data/music_catalog_review.json")
    parser.add_argument("--max-videos-per-channel", type=int)
    parser.add_argument("--max-tracks-per-channel", type=int, default=10)
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    settings: Settings = load_settings()
    service: YouTubeMetadataService | None = None
    try:
        channels = load_official_channels(args.channels_file)
        service = YouTubeMetadataService(
            api_key=settings.youtube_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
        catalog, review = collect_music_catalog(
            channels,
            youtube_service=service,
            max_videos_per_channel=args.max_videos_per_channel,
            max_tracks_per_channel=args.max_tracks_per_channel,
        )
        write_collection_outputs(
            catalog=catalog,
            review=review,
            catalog_path=args.catalog_out,
            review_path=args.review_out,
            force=bool(args.force),
        )
    except (MusicYouTubeCollectorError, YouTubeMetadataError, ValueError) as exc:
        error = {
            "ok": False,
            "error": {
                "source": "music_youtube_collector",
                "code": getattr(exc, "code", "music_youtube_collection_failed"),
                "message": str(exc),
                "retryable": bool(getattr(exc, "retryable", False)),
            },
        }
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        if service is not None:
            service.close()
    print(
        json.dumps(
            {
                "ok": True,
                "catalog_out": args.catalog_out,
                "review_out": args.review_out,
                "selected_track_count": len(catalog["tracks"]),
                "review_required_count": review["review_required_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
