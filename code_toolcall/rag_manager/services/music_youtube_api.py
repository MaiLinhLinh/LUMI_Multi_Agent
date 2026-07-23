"""Minimal YouTube Data API client for verifying catalog source metadata."""

from __future__ import annotations

import re
from typing import Any, Iterable

import httpx


YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
ISO_DURATION_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


class YouTubeMetadataError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class YouTubeMetadataService:
    """Fetch public/embed status and factual source fields in batches of 50."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 20,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("YOUTUBE_API_KEY is required for YouTube verification")
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def fetch_videos(self, video_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        requested = list(dict.fromkeys(str(value).strip() for value in video_ids))
        if any(not VIDEO_ID_PATTERN.fullmatch(value) for value in requested):
            raise ValueError("YouTube metadata request contains an invalid video id")
        found: dict[str, dict[str, Any]] = {}
        for start in range(0, len(requested), 50):
            found.update(self._fetch_batch(requested[start : start + 50]))
        return found

    def fetch_upload_playlists(
        self,
        channel_ids: Iterable[str],
    ) -> dict[str, dict[str, str]]:
        """Resolve each confirmed channel to its canonical Uploads playlist."""

        requested = list(dict.fromkeys(str(value).strip() for value in channel_ids))
        found: dict[str, dict[str, str]] = {}
        for start in range(0, len(requested), 50):
            batch = requested[start : start + 50]
            payload = self._get_json(
                YOUTUBE_CHANNELS_URL,
                params={
                    "part": "snippet,contentDetails",
                    "id": ",".join(batch),
                    "maxResults": 50,
                },
            )
            items = _items(payload)
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    continue
                details = item.get("contentDetails")
                related = details.get("relatedPlaylists") if isinstance(details, dict) else None
                uploads = related.get("uploads") if isinstance(related, dict) else None
                snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
                if isinstance(uploads, str) and uploads.strip():
                    found[item["id"]] = {
                        "channel_id": item["id"],
                        "channel_name": str(snippet.get("title", "")).strip(),
                        "uploads_playlist_id": uploads.strip(),
                    }
        return found

    def fetch_upload_video_ids(
        self,
        playlist_id: str,
        *,
        max_videos: int | None = None,
    ) -> list[str]:
        """Page through an Uploads playlist without using costly global search."""

        if max_videos is not None and max_videos <= 0:
            raise ValueError("max_videos must be positive when provided")
        video_ids: list[str] = []
        seen_ids: set[str] = set()
        page_token = ""
        while True:
            params = {
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._get_json(YOUTUBE_PLAYLIST_ITEMS_URL, params=params)
            for item in _items(payload):
                details = item.get("contentDetails") if isinstance(item, dict) else None
                video_id = details.get("videoId") if isinstance(details, dict) else None
                if isinstance(video_id, str) and VIDEO_ID_PATTERN.fullmatch(video_id):
                    if video_id not in seen_ids:
                        seen_ids.add(video_id)
                        video_ids.append(video_id)
                        if max_videos is not None and len(video_ids) >= max_videos:
                            return video_ids
            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token.strip():
                return video_ids
            page_token = next_token.strip()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _fetch_batch(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not video_ids:
            return {}
        payload = self._get_json(
            YOUTUBE_VIDEOS_URL,
            params={
                "part": "snippet,contentDetails,status,statistics",
                "id": ",".join(video_ids),
                "maxResults": 50,
            },
        )
        return {
            item["id"]: _normalize_video(item)
            for item in _items(payload)
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and VIDEO_ID_PATTERN.fullmatch(item["id"])
        }

    def _get_json(self, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        request_params = {**params, "key": self.api_key}
        try:
            response = self._client.get(
                url,
                params=request_params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise YouTubeMetadataError(
                "youtube_timeout",
                "YouTube metadata request timed out.",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise YouTubeMetadataError(
                "youtube_http_error",
                f"YouTube Data API returned HTTP {exc.response.status_code}.",
                retryable=exc.response.status_code >= 500,
            ) from exc
        except httpx.RequestError as exc:
            raise YouTubeMetadataError(
                "youtube_unavailable",
                f"Could not connect to YouTube Data API: {exc}",
                retryable=True,
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise YouTubeMetadataError(
                "invalid_youtube_response",
                "YouTube Data API response was not valid JSON.",
                retryable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise YouTubeMetadataError(
                "invalid_youtube_response",
                "YouTube Data API response was not an object.",
                retryable=False,
            )
        return payload


def _normalize_video(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    details = (
        item.get("contentDetails")
        if isinstance(item.get("contentDetails"), dict)
        else {}
    )
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    privacy_status = str(status.get("privacyStatus", "")).strip().lower()
    upload_status = str(status.get("uploadStatus", "")).strip().lower()
    embeddable = status.get("embeddable") is True
    return {
        "video_id": item["id"],
        "youtube_title": str(snippet.get("title", "")).strip(),
        "channel_id": str(snippet.get("channelId", "")).strip(),
        "channel_name": str(snippet.get("channelTitle", "")).strip(),
        "published_at": str(snippet.get("publishedAt", "")).strip(),
        "thumbnail_url": _best_thumbnail(snippet.get("thumbnails")),
        "duration_seconds": _parse_iso_duration(details.get("duration")),
        "embeddable": embeddable,
        "source_active": privacy_status == "public"
        and upload_status in {"", "processed"},
        "youtube_privacy_status": privacy_status,
        "youtube_upload_status": upload_status,
        "view_count": _nonnegative_int(statistics.get("viewCount")),
        "like_count": _nonnegative_int(statistics.get("likeCount")),
        "youtube_tags": _string_list(snippet.get("tags")),
    }


def _items(payload: dict[str, Any]) -> list[Any]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise YouTubeMetadataError(
            "invalid_youtube_response",
            "YouTube Data API response did not contain an items list.",
            retryable=False,
        )
    return items


def _best_thumbnail(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    for key in ("maxres", "standard", "high", "medium", "default"):
        candidate = raw.get(key)
        if isinstance(candidate, dict):
            url = candidate.get("url")
            if isinstance(url, str) and url.strip():
                return url.strip()
    return ""


def _parse_iso_duration(raw: Any) -> int:
    value = str(raw or "").strip()
    match = ISO_DURATION_PATTERN.fullmatch(value)
    if match is None:
        return 0
    parts = {key: int(number or 0) for key, number in match.groupdict().items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def _nonnegative_int(raw: Any) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [value.strip() for value in raw if isinstance(value, str) and value.strip()]
