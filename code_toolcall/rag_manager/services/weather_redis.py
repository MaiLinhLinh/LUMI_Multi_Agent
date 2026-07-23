"""Redis-backed active weather snapshot storage."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from rag_manager.config import Settings


REDIS_WEATHER_SOURCE = "weather_redis"
WEATHER_SNAPSHOT_SCHEMA_VERSION = "weather.snapshot.v4"
DEFAULT_WEATHER_SNAPSHOT_MAX_AGE_SECONDS = 14400
WEATHER_SNAPSHOT_FUTURE_TOLERANCE_SECONDS = 300
WEATHER_REDIS_UNAVAILABLE_CODES = frozenset(
    {
        "snapshot_unavailable",
        "snapshot_stale",
        "location_not_in_snapshot",
        "forecast_date_unavailable",
    }
)


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
        max_age_seconds: int = DEFAULT_WEATHER_SNAPSHOT_MAX_AGE_SECONDS,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._prefix = prefix.strip(": ") or "weather"
        self._max_age_seconds = max(1, int(max_age_seconds))
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
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
        return cls(
            client,
            prefix=settings.weather_redis_prefix,
            max_age_seconds=settings.weather_snapshot_max_age_seconds,
        )

    def get_current(self, location_id: str) -> dict[str, Any]:
        return self._finalize_read(
            self._read_location_section(location_id, section="current")
        )

    def get_forecast(
        self,
        location_id: str,
        *,
        days: int,
        start_date: str | None = None,
    ) -> dict[str, Any]:
        response = self._read_location_section(location_id, section="forecast")
        if not response.get("ok"):
            return self._finalize_read(response)

        data = response.get("data")
        if not isinstance(data, dict):
            return self._finalize_read(
                _redis_error(
                    "location_payload_invalid",
                    "Forecast payload in Redis is invalid.",
                    retryable=False,
                )
            )

        try:
            requested_days = int(days)
        except (TypeError, ValueError):
            requested_days = 0
        bounded_days = max(1, min(requested_days, 8))
        forecast = dict(data)
        stored_days = forecast.get("days")
        if not isinstance(stored_days, list):
            return self._finalize_read(
                _redis_error(
                    "location_payload_invalid",
                    "Forecast days in Redis are invalid.",
                    retryable=False,
                )
            )

        if start_date:
            try:
                requested_date = date.fromisoformat(start_date)
            except (TypeError, ValueError):
                return self._finalize_read(
                    _redis_error(
                        "forecast_start_date_invalid",
                        "Forecast start_date must use YYYY-MM-DD format.",
                        retryable=False,
                        details={"requested_start_date": start_date},
                    )
                )
            expected_dates = [
                (requested_date + timedelta(days=offset)).isoformat()
                for offset in range(bounded_days)
            ]
            forecast["requested_start_date"] = requested_date.isoformat()
        else:
            expected_dates = [
                item["date"]
                for item in stored_days[:bounded_days]
                if isinstance(item, dict) and isinstance(item.get("date"), str)
            ]

        days_by_date = {
            item["date"]: item
            for item in stored_days
            if isinstance(item, dict) and isinstance(item.get("date"), str)
        }
        missing_dates = [value for value in expected_dates if value not in days_by_date]
        if len(expected_dates) != bounded_days or missing_dates:
            available_dates = list(days_by_date)
            return self._finalize_read(
                _redis_error(
                    "forecast_date_unavailable",
                    "The active snapshot does not contain the exact requested forecast range.",
                    retryable=True,
                    details={
                        "requested_dates": expected_dates,
                        "available_dates": available_dates,
                        "missing_dates": missing_dates,
                    },
                )
            )

        forecast["days"] = [days_by_date[value] for value in expected_dates]
        forecast["requested_days"] = bounded_days
        result = {**response, "data": forecast}
        return self._finalize_read(result)

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

        published_metadata = dict(metadata)
        supplied_schema = published_metadata.get("schema_version")
        if supplied_schema not in (None, WEATHER_SNAPSHOT_SCHEMA_VERSION):
            raise ValueError("Weather snapshot metadata schema_version is unsupported")
        supplied_snapshot_id = published_metadata.get("snapshot_id")
        if supplied_snapshot_id not in (None, snapshot_id):
            raise ValueError("Weather snapshot metadata snapshot_id does not match")
        supplied_location_count = published_metadata.get("location_count")
        if supplied_location_count not in (None, len(entries)):
            raise ValueError("Weather snapshot metadata location_count does not match")
        published_metadata.update(
            {
                "schema_version": WEATHER_SNAPSHOT_SCHEMA_VERSION,
                "snapshot_id": snapshot_id,
                "location_count": len(entries),
            }
        )

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
            json.dumps(
                published_metadata,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
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

    def _read_location_section(
        self,
        location_id: str,
        *,
        section: str,
    ) -> dict[str, Any]:
        if not location_id.strip():
            return _redis_error(
                "missing_location_id",
                "Missing weather location_id.",
                retryable=False,
            )
        if not _valid_location_id(location_id):
            return _redis_error(
                "invalid_location_id",
                f"Invalid weather location_id {location_id!r}.",
                retryable=False,
                details={"location_id": location_id},
            )

        result: dict[str, Any] | None = None
        for attempt in range(2):
            result = self._read_location_section_once(location_id, section=section)
            error = result.get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
            if code == "snapshot_changed_during_read" and attempt == 0:
                continue
            break
        return result or _redis_error(
            "redis_connection_error",
            "Redis weather lookup did not return a result.",
            retryable=True,
        )

    def _read_location_section_once(
        self,
        location_id: str,
        *,
        section: str,
    ) -> dict[str, Any]:
        try:
            snapshot_id = _decode_redis_text(self._client.get(self._active_key()))
            if not snapshot_id:
                return _redis_error(
                    "snapshot_unavailable",
                    "No active weather snapshot is available in Redis.",
                    retryable=True,
                )
            raw_metadata = self._client.get(self._metadata_key(snapshot_id))
            raw_payload = self._client.get(
                self._location_key(snapshot_id, location_id)
            )
            final_snapshot_id = _decode_redis_text(
                self._client.get(self._active_key())
            )
        except Exception as exc:  # noqa: BLE001 - Redis is an external boundary
            return _redis_error(
                "redis_connection_error",
                f"Redis weather lookup failed: {exc}",
                retryable=True,
                details={"exception_type": exc.__class__.__name__},
            )

        if final_snapshot_id != snapshot_id:
            return _redis_error(
                "snapshot_changed_during_read",
                "The active weather snapshot changed while it was being read.",
                retryable=True,
                details={
                    "snapshot_id_before": snapshot_id,
                    "snapshot_id_after": final_snapshot_id,
                },
            )

        metadata_result = self._validate_metadata(snapshot_id, raw_metadata)
        if not metadata_result.get("ok"):
            return metadata_result
        metadata = metadata_result["metadata"]
        snapshot = metadata_result["snapshot"]

        raw_text = _decode_redis_text(raw_payload)
        if raw_text is None:
            return _redis_error(
                "location_not_in_snapshot",
                f"Location ID {location_id!r} is not present in active weather snapshot.",
                retryable=True,
                details={"snapshot_id": snapshot_id, "location_id": location_id},
            )
        try:
            payload = json.loads(raw_text)
        except (TypeError, ValueError):
            return _redis_error(
                "location_payload_invalid_json",
                "Weather location payload in Redis is not valid JSON.",
                retryable=False,
                details={"snapshot_id": snapshot_id, "location_id": location_id},
            )
        if not isinstance(payload, dict):
            return _redis_error(
                "location_payload_invalid",
                "Weather location payload in Redis must be a JSON object.",
                retryable=False,
                details={"snapshot_id": snapshot_id, "location_id": location_id},
            )

        payload_issue = _validate_location_payload(
            payload,
            metadata=metadata,
            snapshot_id=snapshot_id,
            location_id=location_id,
            requested_section=section,
        )
        if payload_issue is not None:
            return payload_issue

        return {
            "ok": True,
            "data": payload[section],
            "snapshot": snapshot,
            "snapshot_id": snapshot_id,
            "location_id": location_id,
            "cached": True,
        }

    def _validate_metadata(
        self,
        snapshot_id: str,
        raw_metadata: Any,
    ) -> dict[str, Any]:
        raw_text = _decode_redis_text(raw_metadata)
        if raw_text is None:
            return _redis_error(
                "snapshot_metadata_missing",
                "The active weather snapshot metadata is missing.",
                retryable=True,
                details={"snapshot_id": snapshot_id},
            )
        try:
            metadata = json.loads(raw_text)
        except (TypeError, ValueError):
            return _redis_error(
                "snapshot_metadata_invalid_json",
                "The active weather snapshot metadata is not valid JSON.",
                retryable=False,
                details={"snapshot_id": snapshot_id},
            )
        if not isinstance(metadata, dict):
            return _redis_error(
                "snapshot_metadata_invalid_json",
                "The active weather snapshot metadata must be a JSON object.",
                retryable=False,
                details={"snapshot_id": snapshot_id},
            )

        schema_version = metadata.get("schema_version")
        if schema_version != WEATHER_SNAPSHOT_SCHEMA_VERSION:
            return _redis_error(
                "snapshot_schema_unsupported",
                "The active weather snapshot schema is unsupported.",
                retryable=False,
                details={
                    "snapshot_id": snapshot_id,
                    "actual_schema_version": schema_version,
                    "supported_schema_versions": [WEATHER_SNAPSHOT_SCHEMA_VERSION],
                },
            )
        if metadata.get("snapshot_id") != snapshot_id:
            return _redis_error(
                "snapshot_id_mismatch",
                "The active pointer and snapshot metadata IDs do not match.",
                retryable=True,
                details={
                    "active_snapshot_id": snapshot_id,
                    "metadata_snapshot_id": metadata.get("snapshot_id"),
                },
            )

        provider = metadata.get("provider")
        location_count = metadata.get("location_count")
        if (
            not isinstance(provider, str)
            or not provider.strip()
            or isinstance(location_count, bool)
            or not isinstance(location_count, int)
            or location_count < 1
        ):
            return _redis_error(
                "snapshot_metadata_invalid",
                "The active weather snapshot metadata fields are invalid.",
                retryable=False,
                details={
                    "snapshot_id": snapshot_id,
                    "provider": provider,
                    "location_count": location_count,
                },
            )

        generated_at_text = metadata.get("generated_at_utc")
        generated_at = _parse_aware_datetime(generated_at_text)
        if generated_at is None:
            return _redis_error(
                "snapshot_generated_at_invalid",
                "The active weather snapshot generated_at_utc is invalid.",
                retryable=False,
                details={
                    "snapshot_id": snapshot_id,
                    "generated_at_utc": generated_at_text,
                },
            )
        generated_at_utc = generated_at.astimezone(timezone.utc)
        now = self._now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        age_seconds = (now - generated_at_utc).total_seconds()
        if age_seconds < -WEATHER_SNAPSHOT_FUTURE_TOLERANCE_SECONDS:
            return _redis_error(
                "snapshot_generated_at_invalid",
                "The active weather snapshot was generated unexpectedly far in the future.",
                retryable=False,
                details={
                    "snapshot_id": snapshot_id,
                    "generated_at_utc": generated_at_utc.isoformat(),
                    "future_tolerance_seconds": WEATHER_SNAPSHOT_FUTURE_TOLERANCE_SECONDS,
                },
            )
        effective_age_seconds = max(0.0, age_seconds)
        if effective_age_seconds > self._max_age_seconds:
            return _redis_error(
                "snapshot_stale",
                "The active weather snapshot is older than the configured freshness limit.",
                retryable=True,
                details={
                    "snapshot_id": snapshot_id,
                    "age_seconds": round(effective_age_seconds, 3),
                    "max_age_seconds": self._max_age_seconds,
                },
            )

        snapshot = dict(metadata)
        snapshot["generated_at_utc"] = generated_at_utc.isoformat()
        snapshot["age_seconds"] = round(effective_age_seconds, 3)
        return {"ok": True, "metadata": metadata, "snapshot": snapshot}

    def _finalize_read(self, response: dict[str, Any]) -> dict[str, Any]:
        if response.get("ok"):
            self._hits += 1
            return response
        error = response.get("error", {})
        code = error.get("code") if isinstance(error, dict) else None
        if code in WEATHER_REDIS_UNAVAILABLE_CODES or code in {
            "missing_location_id",
            "invalid_location_id",
        }:
            self._misses += 1
        else:
            self._errors += 1
        return response

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
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) and value else None


def _parse_aware_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _timezone_offset(data: dict[str, Any]) -> int | None:
    value = data.get("timezone_offset_seconds", data.get("timezone"))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not numeric.is_integer() or abs(numeric) > 18 * 60 * 60:
        return None
    return int(numeric)


def _validate_location_payload(
    payload: dict[str, Any],
    *,
    metadata: dict[str, Any],
    snapshot_id: str,
    location_id: str,
    requested_section: str,
) -> dict[str, Any] | None:
    payload_schema = payload.get("schema_version")
    if payload_schema != WEATHER_SNAPSHOT_SCHEMA_VERSION:
        return _redis_error(
            "snapshot_schema_unsupported",
            "The weather location payload schema is unsupported.",
            retryable=False,
            details={
                "snapshot_id": snapshot_id,
                "actual_schema_version": payload_schema,
                "supported_schema_versions": [WEATHER_SNAPSHOT_SCHEMA_VERSION],
            },
        )
    if payload.get("snapshot_id") != snapshot_id:
        return _redis_error(
            "snapshot_id_mismatch",
            "The active pointer and weather location payload IDs do not match.",
            retryable=True,
            details={
                "active_snapshot_id": snapshot_id,
                "payload_snapshot_id": payload.get("snapshot_id"),
            },
        )
    if payload.get("location_id") != location_id:
        return _redis_error(
            "location_id_mismatch",
            "The requested and stored weather location IDs do not match.",
            retryable=False,
            details={
                "requested_location_id": location_id,
                "payload_location_id": payload.get("location_id"),
            },
        )

    current = payload.get("current")
    forecast = payload.get("forecast")
    if not isinstance(payload.get(requested_section), dict):
        return _redis_error(
            "weather_section_missing",
            f"Weather section {requested_section!r} is missing in Redis.",
            retryable=False,
            details={"snapshot_id": snapshot_id, "location_id": location_id},
        )
    if not isinstance(current, dict) or not isinstance(forecast, dict):
        return _redis_error(
            "weather_section_missing",
            "Current and forecast sections are both required in a weather snapshot.",
            retryable=False,
            details={"snapshot_id": snapshot_id, "location_id": location_id},
        )

    outer_location = payload.get("location")
    current_location = current.get("location")
    forecast_location = forecast.get("location")
    if (
        not isinstance(outer_location, str)
        or not outer_location.strip()
        or current_location != outer_location
        or forecast_location != outer_location
    ):
        return _redis_error(
            "location_payload_invalid",
            "Current and forecast weather locations are inconsistent.",
            retryable=False,
            details={
                "outer_location": outer_location,
                "current_location": current_location,
                "forecast_location": forecast_location,
            },
        )
    if current.get("location_id") != location_id or forecast.get("location_id") != location_id:
        return _redis_error(
            "location_id_mismatch",
            "Current and forecast location IDs do not match the request.",
            retryable=False,
            details={
                "requested_location_id": location_id,
                "current_location_id": current.get("location_id"),
                "forecast_location_id": forecast.get("location_id"),
            },
        )

    current_timezone = _timezone_offset(current)
    forecast_timezone = _timezone_offset(forecast)
    if current_timezone is None or forecast_timezone is None or current_timezone != forecast_timezone:
        return _redis_error(
            "location_payload_invalid",
            "Current and forecast timezone offsets are missing or inconsistent.",
            retryable=False,
            details={
                "current_timezone_offset_seconds": current_timezone,
                "forecast_timezone_offset_seconds": forecast_timezone,
            },
        )

    forecast_issue = _validate_forecast_days(forecast)
    if forecast_issue is not None:
        return forecast_issue
    if metadata.get("snapshot_id") != payload.get("snapshot_id"):
        return _redis_error(
            "snapshot_id_mismatch",
            "Snapshot metadata and location payload IDs do not match.",
            retryable=True,
        )
    return None


def _validate_forecast_days(forecast: dict[str, Any]) -> dict[str, Any] | None:
    days = forecast.get("days")
    if not isinstance(days, list):
        return _redis_error(
            "location_payload_invalid",
            "Forecast days must be an array.",
            retryable=False,
        )

    parsed_dates: list[date] = []
    all_intervals: list[dict[str, Any]] = []
    for day_index, day_payload in enumerate(days):
        if not isinstance(day_payload, dict):
            return _forecast_payload_issue("Forecast days must contain JSON objects.")
        day_text = day_payload.get("date")
        try:
            parsed_date = date.fromisoformat(day_text)
        except (TypeError, ValueError):
            return _forecast_payload_issue(
                "Forecast day date must use YYYY-MM-DD format.",
                details={"day_index": day_index, "date": day_text},
            )
        if parsed_dates and parsed_date <= parsed_dates[-1]:
            return _forecast_payload_issue(
                "Forecast dates must be unique and strictly increasing.",
                details={"day_index": day_index, "date": day_text},
            )
        parsed_dates.append(parsed_date)

        intervals = day_payload.get("intervals")
        if not isinstance(intervals, list):
            return _forecast_payload_issue(
                "Forecast day intervals must be an array.",
                details={"date": day_text},
            )
        interval_count = day_payload.get("interval_count")
        if (
            isinstance(interval_count, bool)
            or not isinstance(interval_count, int)
            or interval_count != len(intervals)
        ):
            return _forecast_payload_issue(
                "Forecast day interval_count does not match its intervals.",
                details={
                    "date": day_text,
                    "interval_count": interval_count,
                    "actual_interval_count": len(intervals),
                },
            )
        previous_interval: datetime | None = None
        for interval_index, interval in enumerate(intervals):
            if not isinstance(interval, dict):
                return _forecast_payload_issue(
                    "Forecast intervals must contain JSON objects.",
                    details={"date": day_text, "interval_index": interval_index},
                )
            forecast_at_local = _parse_aware_datetime(interval.get("forecast_at_local"))
            if (
                interval.get("local_date") != day_text
                or forecast_at_local is None
                or forecast_at_local.date() != parsed_date
            ):
                return _forecast_payload_issue(
                    "A forecast interval does not belong to its local forecast date.",
                    details={"date": day_text, "interval_index": interval_index},
                )
            if previous_interval is not None and forecast_at_local <= previous_interval:
                return _forecast_payload_issue(
                    "Forecast intervals must be strictly increasing.",
                    details={"date": day_text, "interval_index": interval_index},
                )
            previous_interval = forecast_at_local
            all_intervals.append(interval)

    available_day_count = forecast.get("available_day_count")
    interval_count = forecast.get("interval_count")
    if (
        isinstance(available_day_count, bool)
        or not isinstance(available_day_count, int)
        or available_day_count != len(days)
        or isinstance(interval_count, bool)
        or not isinstance(interval_count, int)
        or interval_count != len(all_intervals)
    ):
        return _forecast_payload_issue(
            "Forecast coverage counts do not match the stored data.",
            details={
                "available_day_count": available_day_count,
                "actual_day_count": len(days),
                "interval_count": interval_count,
                "actual_interval_count": len(all_intervals),
            },
        )

    expected_start = all_intervals[0].get("forecast_at_local") if all_intervals else None
    expected_end = all_intervals[-1].get("forecast_at_local") if all_intervals else None
    if (
        forecast.get("coverage_start_local") != expected_start
        or forecast.get("coverage_end_local") != expected_end
    ):
        return _forecast_payload_issue(
            "Forecast coverage timestamps do not match the stored intervals.",
            details={
                "coverage_start_local": forecast.get("coverage_start_local"),
                "expected_coverage_start_local": expected_start,
                "coverage_end_local": forecast.get("coverage_end_local"),
                "expected_coverage_end_local": expected_end,
            },
        )
    return None


def _forecast_payload_issue(
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _redis_error(
        "location_payload_invalid",
        message,
        retryable=False,
        details=details,
    )


def _redis_error(
    code: str,
    message: str,
    *,
    retryable: bool,
    status_code: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "source": REDIS_WEATHER_SOURCE,
            "code": code,
            "message": message,
            "retryable": retryable,
            "status_code": status_code,
            "details": details or {},
        },
    }
