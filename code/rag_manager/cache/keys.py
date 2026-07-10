"""Cache key builders for external data sources."""

from __future__ import annotations

from datetime import datetime, timezone
import re


def weather_cache_key(
    location: str,
    bucket: str = "",
    now: datetime | None = None,
) -> str:
    if not bucket:
        bucket = weather_hour_bucket(now)
    return _join_key_parts("weather", normalize_location(location), bucket)


def news_cache_key(
    query: str,
    bucket: str = "",
    now: datetime | None = None,
) -> str:
    if not bucket:
        bucket = news_15_minute_bucket(now)
    return _join_key_parts("news", normalize_query(query), bucket)


def wiki_cache_key(topic: str) -> str:
    return _join_key_parts("wiki", normalize_topic(topic))


def normalize_location(location: str) -> str:
    return normalize_cache_text(location)


def normalize_query(query: str) -> str:
    return normalize_cache_text(query)


def normalize_topic(topic: str) -> str:
    return normalize_cache_text(topic)


def normalize_cache_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def weather_hour_bucket(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return current.strftime("%Y%m%d%H")


def news_15_minute_bucket(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    minute_bucket = current.minute - (current.minute % 15)
    return current.replace(minute=minute_bucket, second=0, microsecond=0).strftime(
        "%Y%m%d%H%M"
    )


def _join_key_parts(*parts: str) -> str:
    return ":".join(part for part in parts if part)
