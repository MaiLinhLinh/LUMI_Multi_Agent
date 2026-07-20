"""Build a small, validated payload for the frontend YouTube player."""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse


MUSIC_PLAYER_SCHEMA_VERSION = "music.youtube-player.v1"
YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
BACKEND_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
SAFE_THUMBNAIL_HOSTS = {"i.ytimg.com", "img.youtube.com"}
PLAYER_ACTIONS = {"play", "replay", "stop"}
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


class MusicPlayerPayloadError(ValueError):
    """A selected backend candidate cannot safely reach the player UI."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = False


def build_music_player_payload(
    candidate: Mapping[str, Any],
    *,
    player_action: str = "play",
) -> dict[str, Any]:
    """Return metadata only; the frontend owns the fixed iframe URL template."""

    if player_action not in PLAYER_ACTIONS:
        raise MusicPlayerPayloadError(
            "invalid_player_action",
            "Music player action is not supported.",
        )
    video_id = _required_text(candidate.get("video_id"), "video_id", max_length=64)
    if not YOUTUBE_VIDEO_ID_PATTERN.fullmatch(video_id):
        raise MusicPlayerPayloadError(
            "invalid_youtube_video_id",
            "Selected Music candidate has an invalid YouTube video ID.",
        )
    source_id = _backend_id(candidate.get("record_id"), "record_id")
    track_id = _backend_id(candidate.get("track_id"), "track_id")
    title = _required_text(candidate.get("title"), "title", max_length=300)
    artists = _artists(candidate.get("artists"))
    content_type = _required_text(
        candidate.get("content_type"),
        "content_type",
        max_length=40,
    ).lower()
    if content_type not in CONTENT_TYPES:
        raise MusicPlayerPayloadError(
            "invalid_music_content_type",
            "Selected Music candidate has an unsupported content type.",
        )

    music: dict[str, Any] = {
        "source_id": source_id,
        "track_id": track_id,
        "title": title,
        "artist": ", ".join(artists),
        "artists": artists,
        "video_id": video_id,
        "content_type": content_type,
        "version": _optional_text(candidate.get("version"), max_length=80),
        "duration_seconds": _optional_positive_int(
            candidate.get("duration_seconds")
        ),
        "release_date": _optional_text(
            candidate.get("release_date"),
            max_length=40,
        ),
    }
    thumbnail_url = _safe_thumbnail(candidate.get("thumbnail_url"), video_id)
    if thumbnail_url:
        music["thumbnail_url"] = thumbnail_url

    return {
        "schema_version": MUSIC_PLAYER_SCHEMA_VERSION,
        "status": "completed",
        "ui_type": "youtube_player",
        "player_action": player_action,
        "music": music,
    }


def _backend_id(raw: Any, field: str) -> str:
    value = _required_text(raw, field, max_length=128)
    if not BACKEND_ID_PATTERN.fullmatch(value):
        raise MusicPlayerPayloadError(
            "invalid_music_backend_id",
            f"Selected Music candidate has an invalid {field}.",
        )
    return value


def _artists(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise MusicPlayerPayloadError(
            "missing_music_artist",
            "Selected Music candidate has no artist list.",
        )
    artists = [
        _required_text(value, "artist", max_length=200)
        for value in raw[:10]
        if isinstance(value, str) and value.strip()
    ]
    if not artists:
        raise MusicPlayerPayloadError(
            "missing_music_artist",
            "Selected Music candidate has no artist.",
        )
    return artists


def _safe_thumbnail(raw: Any, video_id: str) -> str:
    value = _optional_text(raw, max_length=2_000)
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in SAFE_THUMBNAIL_HOSTS:
        return ""
    path = parsed.path.casefold()
    if f"/{video_id.casefold()}/" not in path:
        return ""
    return value


def _required_text(raw: Any, field: str, *, max_length: int) -> str:
    value = _optional_text(raw, max_length=max_length)
    if not value:
        raise MusicPlayerPayloadError(
            "missing_music_player_field",
            f"Selected Music candidate is missing {field}.",
        )
    return value


def _optional_text(raw: Any, *, max_length: int) -> str:
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if len(value) > max_length or any(ord(char) < 32 for char in value):
        return ""
    return value


def _optional_positive_int(raw: Any) -> int | None:
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return None
