"""Redis-backed active weather snapshot storage."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from rag_manager.config import Settings


REDIS_WEATHER_SOURCE = "weather_redis"
WEATHER_SNAPSHOT_SCHEMA_VERSION = "weather.snapshot.v3"


class WeatherStore(Protocol):
    """Interface consumed by Weather Agent tools."""

    def get_current(self, location_id: str) -> dict[str, Any]: ...

    def get_forecast(
        self,
        location_id: str,
        *,
        days: int,
        start_date: str | None = None,
    ) -> dict[str, Any]: ...

    def stats(self) -> dict[str, int]: ...


@dataclass(frozen=True)
class WeatherSnapshotEntry:
    """One location written into a snapshot."""

    location_id: str
    location: str
    current: dict[str, Any]
    forecast: dict[str, Any]
    raw_current: dict[str, Any] | None = None
    raw_forecast: dict[str, Any] | None = None


class RedisWeatherStore:
    """Read and atomically publish versioned weather snapshots in Redis."""

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "weather",
    ) -> None:
        self._client = client
        self._prefix = prefix.strip(": ") or "weather"
        self._hits = 0
        self._misses = 0
        self._errors = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> RedisWeatherStore:
        """Create a Redis store without connecting until the first command."""

        try:
            from redis import Redis
        except ImportError as exc:  # pragma: no cover - dependency is runtime setup
            raise RuntimeError(
                "Redis support is not installed. Run: pip install -r requirements.txt"
            ) from exc

        client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.request_timeout_seconds,
            socket_timeout=settings.request_timeout_seconds,
        )
        return cls(client, prefix=settings.weather_redis_prefix)

    def get_current(self, location_id: str) -> dict[str, Any]:
        return self._read_location_section(location_id, section="current")

    def get_forecast(
        self,
        location_id: str,
        *,
        days: int,
        start_date: str | None = None,
    ) -> dict[str, Any]:
        response = self._read_location_section(location_id, section="forecast")
        if not response.get("ok"):
            return response

        data = response.get("data")
        if not isinstance(data, dict):
            self._errors += 1
            return _redis_error("Forecast payload in Redis is invalid.")

        bounded_days = max(1, min(int(days), 5))
        forecast = dict(data)
        stored_days = forecast.get("days")
        if isinstance(stored_days, list):
            selected_days = stored_days
            if start_date:
                try:
                    requested_date = date.fromisoformat(start_date)
                except ValueError:
                    self._errors += 1
                    return _redis_error("Forecast start_date must use YYYY-MM-DD format.")
                selected_days = [
                    day
                    for day in stored_days
                    if isinstance(day, dict)
                    and isinstance(day.get("date"), str)
                    and day["date"] >= requested_date.isoformat()
                ]
                forecast["requested_start_date"] = requested_date.isoformat()
            forecast["days"] = selected_days[:bounded_days]
        forecast["requested_days"] = bounded_days
        return {**response, "data": forecast}

    def save_snapshot(
        self,
        snapshot_id: str,
        entries: list[WeatherSnapshotEntry],
        *,
        metadata: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        """Write all records and switch the active pointer in one transaction."""

        if not snapshot_id.strip():
            raise ValueError("snapshot_id must not be empty")
        if not entries:
            raise ValueError("A weather snapshot must contain at least one location")
        location_ids = [entry.location_id for entry in entries]
        if any(not _valid_location_id(location_id) for location_id in location_ids):
            raise ValueError("Every weather snapshot entry requires a valid location_id")
        if len(set(location_ids)) != len(location_ids):
            raise ValueError("Weather snapshot location_ids must be unique")
        ttl = max(1, int(ttl_seconds))
        pipeline = self._client.pipeline(transaction=True)
        for entry in entries:
            payload = json.dumps(
                {
                    "schema_version": WEATHER_SNAPSHOT_SCHEMA_VERSION,
                    "snapshot_id": snapshot_id,
                    "location_id": entry.location_id,
                    "location": entry.location,
                    "current": entry.current,
                    "forecast": entry.forecast,
                    "raw": {
                        "current": entry.raw_current,
                        "forecast": entry.raw_forecast,
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            pipeline.setex(
                self._location_key(snapshot_id, entry.location_id),
                ttl,
                payload,
            )

        pipeline.setex(
            self._metadata_key(snapshot_id),
            ttl,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        )
        pipeline.setex(self._active_key(), ttl, snapshot_id)
        pipeline.execute()

    def active_snapshot_id(self) -> str | None:
        try:
            value = self._client.get(self._active_key())
        except Exception:  # noqa: BLE001 - normalize Redis client failures
            self._errors += 1
            return None
        return _decode_redis_text(value)

    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "errors": self._errors,
        }

    def _read_location_section(self, location_id: str, *, section: str) -> dict[str, Any]:
        if not location_id.strip():
            self._misses += 1
            return _redis_error("Missing weather location_id.")
        if not _valid_location_id(location_id):
            self._misses += 1
            return _redis_error(f"Invalid weather location_id {location_id!r}.")
        try:
            snapshot_id = _decode_redis_text(self._client.get(self._active_key()))
            if not snapshot_id:
                self._misses += 1
                return _redis_error("No active weather snapshot is available in Redis.")
            raw_payload = self._client.get(self._location_key(snapshot_id, location_id))
        except Exception as exc:  # noqa: BLE001 - Redis is an external boundary
            self._errors += 1
            return _redis_error(f"Redis weather lookup failed: {exc}")

        raw_text = _decode_redis_text(raw_payload)
        if raw_text is None:
            self._misses += 1
            return _redis_error(
                f"Location ID {location_id!r} is not present in active weather snapshot."
            )
        try:
            payload = json.loads(raw_text)
        except (TypeError, ValueError):
            self._errors += 1
            return _redis_error("Weather payload in Redis is not valid JSON.")
        if not isinstance(payload, dict) or not isinstance(payload.get(section), dict):
            self._errors += 1
            return _redis_error(f"Weather section {section!r} is missing in Redis.")

        self._hits += 1
        return {
            "ok": True,
            "data": payload[section],
            "snapshot_id": snapshot_id,
            "location_id": payload.get("location_id", location_id),
            "cached": True,
        }

    def _active_key(self) -> str:
        return f"{self._prefix}:snapshot:active"

    def _metadata_key(self, snapshot_id: str) -> str:
        return f"{self._prefix}:snapshot:{snapshot_id}:metadata"

    def _location_key(self, snapshot_id: str, location_id: str) -> str:
        return f"{self._prefix}:snapshot:{snapshot_id}:location:{location_id}"


def location_slug(location: str) -> str:
    """Normalize Vietnamese and ASCII location variants into a Redis key suffix."""

    normalized = location.casefold().replace("đ", "d")
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFD", normalized)
        if unicodedata.category(character) != "Mn"
    )
    normalized = re.sub(r"\b(?:thanh pho|tinh)\b", " ", normalized)
    normalized = re.sub(r"\b(?:viet nam|vietnam|vn)\b$", " ", normalized)
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


def _valid_location_id(location_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", location_id))


def _decode_redis_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value if isinstance(value, str) and value else None


def _redis_error(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "source": REDIS_WEATHER_SOURCE,
            "message": message,
            "status_code": None,
        },
    }
