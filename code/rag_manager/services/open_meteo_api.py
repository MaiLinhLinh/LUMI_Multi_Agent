"""Open-Meteo client used by the Redis weather snapshot worker."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from rag_manager.services.http_client import ServiceResponse, get_json


OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_SOURCE = "open-meteo"
# Keep today plus eight future calendar days. A request may still return at
# most eight days, but this extra source day lets an eight-day range start
# tomorrow without losing its final date.
DEFAULT_FORECAST_DAYS = 9

_CURRENT_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "surface_pressure",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
)
_HOURLY_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "surface_pressure",
    "precipitation_probability",
    "rain",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
)


class OpenMeteoNormalizationError(ValueError):
    """Raised when an Open-Meteo payload cannot be normalized safely."""


def fetch_open_meteo_weather(
    *,
    latitude: float,
    longitude: float,
    timeout_seconds: float = 8,
    forecast_days: int = DEFAULT_FORECAST_DAYS,
) -> ServiceResponse:
    """Fetch current and hourly weather in one coordinate-based request."""

    if not _valid_coordinate(latitude, minimum=-90, maximum=90):
        return _open_meteo_error("Weather latitude is invalid.")
    if not _valid_coordinate(longitude, minimum=-180, maximum=180):
        return _open_meteo_error("Weather longitude is invalid.")

    bounded_days = max(1, min(int(forecast_days), 16))
    response = get_json(
        OPEN_METEO_FORECAST_URL,
        source=OPEN_METEO_SOURCE,
        params={
            "latitude": float(latitude),
            "longitude": float(longitude),
            "current": ",".join(_CURRENT_FIELDS),
            "hourly": ",".join(_HOURLY_FIELDS),
            "forecast_days": bounded_days,
            "timezone": "auto",
            "temperature_unit": "celsius",
            "wind_speed_unit": "ms",
            "precipitation_unit": "mm",
            "timeformat": "iso8601",
        },
        timeout_seconds=timeout_seconds,
    )
    _print_raw_api_response(
        latitude=float(latitude),
        longitude=float(longitude),
        response=response,
    )
    if not response.get("ok"):
        return response

    raw_data = response["data"]
    try:
        current = compact_open_meteo_current(raw_data)
        forecast = compact_open_meteo_forecast(
            raw_data,
            requested_days=bounded_days,
        )
    except OpenMeteoNormalizationError as exc:
        error_response = _open_meteo_error(
            f"Invalid Open-Meteo weather data: {exc}"
        )
        error_response["raw_data"] = raw_data
        return error_response

    return {
        "ok": True,
        "data": {"current": current, "forecast": forecast},
        "raw_data": raw_data,
        "raw_current": {
            "latitude": raw_data.get("latitude"),
            "longitude": raw_data.get("longitude"),
            "timezone": raw_data.get("timezone"),
            "utc_offset_seconds": raw_data.get("utc_offset_seconds"),
            "current_units": raw_data.get("current_units"),
            "current": raw_data.get("current"),
        },
        "raw_forecast": {
            "latitude": raw_data.get("latitude"),
            "longitude": raw_data.get("longitude"),
            "timezone": raw_data.get("timezone"),
            "utc_offset_seconds": raw_data.get("utc_offset_seconds"),
            "hourly_units": raw_data.get("hourly_units"),
            "hourly": raw_data.get("hourly"),
        },
    }


def compact_open_meteo_current(data: dict[str, Any]) -> dict[str, Any]:
    current = _dict_field(data, "current")
    offset_seconds = _validated_timezone_offset(data.get("utc_offset_seconds"))
    local_datetime, utc_datetime = _provider_datetimes(
        current.get("time"),
        offset_seconds,
    )

    return {
        "location": "",
        "country": "VN",
        "timestamp": int(utc_datetime.timestamp()),
        "timezone": offset_seconds,
        "timezone_name": _string_or_empty(data.get("timezone")),
        "timezone_offset_seconds": offset_seconds,
        "observed_at_utc": utc_datetime.isoformat(),
        "observed_at_local": local_datetime.isoformat(),
        "condition": _weather_condition(current.get("weather_code")),
        "temperature": {
            "current_celsius": current.get("temperature_2m"),
            "feels_like_celsius": current.get("apparent_temperature"),
            "min_celsius": None,
            "max_celsius": None,
        },
        "humidity_percent": current.get("relative_humidity_2m"),
        "pressure_hpa": current.get("surface_pressure"),
        "wind": {
            "speed_mps": current.get("wind_speed_10m"),
            "degrees": current.get("wind_direction_10m"),
        },
        "cloudiness_percent": current.get("cloud_cover"),
    }


def compact_open_meteo_forecast(
    data: dict[str, Any],
    *,
    requested_days: int | None = None,
) -> dict[str, Any]:
    hourly = _dict_field(data, "hourly")
    raw_times = hourly.get("time")
    if not isinstance(raw_times, list) or not raw_times:
        raise OpenMeteoNormalizationError("hourly.time is missing or empty")

    for field in _HOURLY_FIELDS:
        values = hourly.get(field)
        if not isinstance(values, list) or len(values) != len(raw_times):
            raise OpenMeteoNormalizationError(
                f"hourly.{field} must match hourly.time length"
            )

    offset_seconds = _validated_timezone_offset(data.get("utc_offset_seconds"))
    intervals_by_date: dict[str, list[dict[str, Any]]] = {}
    for index, raw_time in enumerate(raw_times):
        local_datetime, utc_datetime = _provider_datetimes(
            raw_time,
            offset_seconds,
        )
        local_date = local_datetime.date().isoformat()
        interval = {
            "timestamp": int(utc_datetime.timestamp()),
            "time": local_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "provider_time_local": raw_time,
            "forecast_at_utc": utc_datetime.isoformat(),
            "forecast_at_local": local_datetime.isoformat(),
            "local_date": local_date,
            "condition": _weather_condition(_hourly_value(hourly, "weather_code", index)),
            "temperature_celsius": _hourly_value(hourly, "temperature_2m", index),
            "feels_like_celsius": _hourly_value(
                hourly, "apparent_temperature", index
            ),
            "humidity_percent": _hourly_value(
                hourly, "relative_humidity_2m", index
            ),
            "pressure_hpa": _hourly_value(hourly, "surface_pressure", index),
            "rain_probability": _probability_ratio(
                _hourly_value(hourly, "precipitation_probability", index)
            ),
            "rain_1h_mm": _non_negative_number(
                _hourly_value(hourly, "rain", index)
            ),
            "wind_speed_mps": _hourly_value(hourly, "wind_speed_10m", index),
            "wind_degrees": _hourly_value(
                hourly, "wind_direction_10m", index
            ),
            "cloudiness_percent": _hourly_value(hourly, "cloud_cover", index),
        }
        intervals_by_date.setdefault(local_date, []).append(interval)

    days = [
        _compact_daily_forecast(day_text, intervals)
        for day_text, intervals in sorted(intervals_by_date.items())
    ]
    all_intervals = [interval for day in days for interval in day["intervals"]]

    return {
        "location": "",
        "country": "VN",
        "timezone": offset_seconds,
        "timezone_name": _string_or_empty(data.get("timezone")),
        "timezone_offset_seconds": offset_seconds,
        "requested_days": requested_days,
        "available_day_count": len(days),
        "interval_count": len(all_intervals),
        "coverage_start_local": all_intervals[0]["forecast_at_local"],
        "coverage_end_local": all_intervals[-1]["forecast_at_local"],
        "source_granularity": "1-hour forecast intervals",
        "day_grouping": "location_local_date",
        "interval_time_basis": "location_local_time",
        "days": days,
    }


def _compact_daily_forecast(
    day_text: str,
    intervals: list[dict[str, Any]],
) -> dict[str, Any]:
    temperatures = _number_values(
        interval.get("temperature_celsius") for interval in intervals
    )
    feels_like = _number_values(
        interval.get("feels_like_celsius") for interval in intervals
    )
    rain_probabilities = _number_values(
        interval.get("rain_probability") for interval in intervals
    )
    rain_amounts = _number_values(
        interval.get("rain_1h_mm") for interval in intervals
    )
    descriptions = [
        str(interval.get("condition", {}).get("description", ""))
        for interval in intervals
        if interval.get("condition", {}).get("description")
    ]
    representative = max(
        intervals,
        key=lambda interval: (
            _number_or_default(interval.get("rain_probability")),
            _number_or_default(interval.get("temperature_celsius")),
        ),
        default={},
    )

    return {
        "date": day_text,
        "day_grouping": "location_local_date",
        "interval_count": len(intervals),
        "coverage_start_local": intervals[0]["forecast_at_local"],
        "coverage_end_local": intervals[-1]["forecast_at_local"],
        "is_partial_day": len(intervals) < 24,
        "temperature": {
            "min_celsius": min(temperatures) if temperatures else None,
            "max_celsius": max(temperatures) if temperatures else None,
        },
        "max_rain_probability": (
            max(rain_probabilities) if rain_probabilities else None
        ),
        "total_rain_mm": (
            round(sum(rain_amounts), 2)
            if intervals and len(rain_amounts) == len(intervals)
            else None
        ),
        "rain_data_complete": bool(intervals)
        and len(rain_amounts) == len(intervals),
        "common_conditions": sorted(set(descriptions)),
        "condition": representative.get("condition", {}),
        "temperature_feels_like_celsius": (
            max(feels_like) if feels_like else None
        ),
        "humidity_percent": _average_number(
            interval.get("humidity_percent") for interval in intervals
        ),
        "pressure_hpa": _average_number(
            interval.get("pressure_hpa") for interval in intervals
        ),
        "wind_speed_mps": _average_number(
            interval.get("wind_speed_mps") for interval in intervals
        ),
        "intervals": intervals,
    }


def _provider_datetimes(
    value: Any,
    offset_seconds: int,
) -> tuple[datetime, datetime]:
    if not isinstance(value, str) or not value.strip():
        raise OpenMeteoNormalizationError("provider time is missing or invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise OpenMeteoNormalizationError(
            f"provider time {value!r} is not ISO-8601"
        ) from exc
    local_timezone = timezone(timedelta(seconds=offset_seconds))
    local_datetime = (
        parsed.replace(tzinfo=local_timezone)
        if parsed.tzinfo is None
        else parsed.astimezone(local_timezone)
    )
    return local_datetime, local_datetime.astimezone(timezone.utc)


def _validated_timezone_offset(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpenMeteoNormalizationError(
            "utc_offset_seconds is missing or invalid"
        )
    numeric_value = float(value)
    if not numeric_value.is_integer() or abs(numeric_value) > 18 * 60 * 60:
        raise OpenMeteoNormalizationError("utc_offset_seconds is outside valid range")
    return int(numeric_value)


def _hourly_value(hourly: dict[str, Any], field: str, index: int) -> Any:
    return hourly[field][index]


def _probability_ratio(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= float(value) <= 100:
        return None
    return float(value) / 100


def _non_negative_number(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or float(value) < 0
    ):
        return None
    return float(value)


def _number_values(values: Any) -> list[float]:
    return [
        float(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]


def _number_or_default(value: Any) -> float:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else -1.0
    )


def _average_number(values: Any) -> float | None:
    numbers = _number_values(values)
    return round(sum(numbers) / len(numbers), 2) if numbers else None


def _dict_field(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise OpenMeteoNormalizationError(f"{key} is missing or invalid")
    return value


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _valid_coordinate(value: Any, *, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and minimum <= float(value) <= maximum
    )


def _weather_condition(code: Any) -> dict[str, str]:
    try:
        numeric_code = int(code)
    except (TypeError, ValueError):
        return {"main": "Unknown", "description": "không xác định"}

    if numeric_code == 0:
        return {"main": "Clear", "description": "trời quang"}
    if numeric_code in {1, 2, 3}:
        descriptions = {1: "ít mây", 2: "mây rải rác", 3: "nhiều mây"}
        return {"main": "Clouds", "description": descriptions[numeric_code]}
    if numeric_code in {45, 48}:
        return {"main": "Fog", "description": "sương mù"}
    if numeric_code in {51, 53, 55, 56, 57}:
        return {"main": "Drizzle", "description": "mưa phùn"}
    if numeric_code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return {"main": "Rain", "description": "mưa"}
    if numeric_code in {71, 73, 75, 77, 85, 86}:
        return {"main": "Snow", "description": "tuyết"}
    if numeric_code in {95, 96, 99}:
        return {"main": "Thunderstorm", "description": "dông"}
    return {"main": "Unknown", "description": "không xác định"}


def _open_meteo_error(message: str) -> ServiceResponse:
    return {
        "ok": False,
        "error": {
            "source": OPEN_METEO_SOURCE,
            "message": message,
            "status_code": None,
        },
    }


def _print_raw_api_response(
    *,
    latitude: float,
    longitude: float,
    response: ServiceResponse,
) -> None:
    """Print the provider response before compaction, matching legacy behavior."""

    raw_text = response.get("raw_text")
    if isinstance(raw_text, str) and raw_text.strip():
        body = raw_text
    else:
        payload: Any = (
            response.get("data") if response.get("ok") else response.get("error")
        )
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    message = (
        "[Open-Meteo][RAW_API_RESPONSE] "
        f"latitude={latitude} longitude={longitude}\n{body}"
    )
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            raise
        buffer.write((message + "\n").encode("utf-8"))
        buffer.flush()
