"""OpenWeatherMap service client."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from rag_manager.services.http_client import ServiceResponse, get_json, get_json_list


OPENWEATHER_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
OPENWEATHER_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
OPENWEATHER_GEOCODING_URL = "https://api.openweathermap.org/geo/1.0/direct"
WEATHER_SOURCE = "weather"


class WeatherNormalizationError(ValueError):
    """Raised when provider timestamps cannot be normalized without guessing."""


def geocode_weather_location(
    location: str,
    *,
    api_key: str,
    timeout_seconds: float = 8,
    limit: int = 5,
) -> dict[str, Any]:
    """Return normalized OpenWeather geocoding candidates for one location."""

    if not api_key.strip():
        return _weather_error("Missing OPENWEATHER_API_KEY.")
    if not location.strip():
        return _weather_error("Missing weather location.")

    response = get_json_list(
        OPENWEATHER_GEOCODING_URL,
        source=WEATHER_SOURCE,
        params={
            "q": location,
            "limit": max(1, min(int(limit), 5)),
            "appid": api_key,
        },
        timeout_seconds=timeout_seconds,
    )
    if not response.get("ok"):
        return response

    candidates = [
        _compact_geocoding_candidate(candidate)
        for candidate in response.get("data", [])
    ]
    return {
        "ok": True,
        "data": {
            "query": location,
            "candidates": candidates,
        },
    }


def fetch_weather(
    location: str,
    *,
    api_key: str,
    timeout_seconds: float = 8,
    latitude: float | None = None,
    longitude: float | None = None,
) -> ServiceResponse:
    if not api_key.strip():
        return _weather_error("Missing OPENWEATHER_API_KEY.")
    query_params = _weather_location_params(
        location,
        latitude=latitude,
        longitude=longitude,
    )
    if isinstance(query_params, str):
        return _weather_error(query_params)

    response = get_json(
        OPENWEATHER_CURRENT_URL,
        source=WEATHER_SOURCE,
        params={
            **query_params,
            "appid": api_key,
            "units": "metric",
            "lang": "vi",
        },
        timeout_seconds=timeout_seconds,
    )
    _print_raw_api_response("current", location, response)
    if not response.get("ok"):
        return response
    return {
        "ok": True,
        "data": compact_weather_data(response["data"]),
        "raw_data": response["data"],
    }


def fetch_weather_forecast(
    location: str,
    *,
    api_key: str,
    timeout_seconds: float = 8,
    days: int | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> ServiceResponse:
    if not api_key.strip():
        return _weather_error("Missing OPENWEATHER_API_KEY.")
    query_params = _weather_location_params(
        location,
        latitude=latitude,
        longitude=longitude,
    )
    if isinstance(query_params, str):
        return _weather_error(query_params)

    bounded_days = max(1, min(int(days), 5)) if days is not None else None
    params: dict[str, Any] = {
        **query_params,
        "appid": api_key,
        "units": "metric",
        "lang": "vi",
    }
    if bounded_days is not None:
        params["cnt"] = bounded_days * 8
    response = get_json(
        OPENWEATHER_FORECAST_URL,
        source=WEATHER_SOURCE,
        params=params,
        timeout_seconds=timeout_seconds,
    )
    _print_raw_api_response("forecast", location, response)
    if not response.get("ok"):
        return response
    try:
        normalized_data = compact_forecast_data(
            response["data"],
            days=bounded_days,
        )
    except WeatherNormalizationError as exc:
        error_response = _weather_error(
            f"Invalid OpenWeather forecast data: {exc}"
        )
        error_response["raw_data"] = response["data"]
        return error_response
    return {
        "ok": True,
        "data": normalized_data,
        "raw_data": response["data"],
    }


def _weather_location_params(
    location: str,
    *,
    latitude: float | None,
    longitude: float | None,
) -> dict[str, str | float] | str:
    """Build either a coordinate query or the legacy location-name query."""

    has_latitude = latitude is not None
    has_longitude = longitude is not None
    if has_latitude != has_longitude:
        return "Both weather latitude and longitude are required."
    if has_latitude and has_longitude:
        if not _valid_coordinate(latitude, minimum=-90, maximum=90):
            return "Weather latitude is invalid."
        if not _valid_coordinate(longitude, minimum=-180, maximum=180):
            return "Weather longitude is invalid."
        return {"lat": float(latitude), "lon": float(longitude)}
    if not location.strip():
        return "Missing weather location."
    return {"q": location}


def _valid_coordinate(value: Any, *, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and minimum <= float(value) <= maximum
    )


def _compact_geocoding_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    local_names = candidate.get("local_names")
    return {
        "name": candidate.get("name", ""),
        "local_names": local_names if isinstance(local_names, dict) else {},
        "state": candidate.get("state", ""),
        "country": candidate.get("country", ""),
        "lat": candidate.get("lat"),
        "lon": candidate.get("lon"),
    }


def compact_weather_data(data: dict[str, Any]) -> dict[str, Any]:
    weather_items = data.get("weather")
    first_weather = weather_items[0] if isinstance(weather_items, list) and weather_items else {}
    main = _dict_field(data, "main")
    wind = _dict_field(data, "wind")
    clouds = _dict_field(data, "clouds")
    sys = _dict_field(data, "sys")
    timestamp = data.get("dt")
    timezone_offset = data.get("timezone")

    return {
        "location": data.get("name", ""),
        "country": sys.get("country", ""),
        "timestamp": timestamp,
        "timezone": timezone_offset,
        "timezone_offset_seconds": timezone_offset,
        **_timestamp_iso_fields(
            timestamp,
            timezone_offset,
            utc_field="observed_at_utc",
            local_field="observed_at_local",
        ),
        "condition": {
            "main": first_weather.get("main", ""),
            "description": first_weather.get("description", ""),
        },
        "temperature": {
            "current_celsius": main.get("temp"),
            "feels_like_celsius": main.get("feels_like"),
            "min_celsius": main.get("temp_min"),
            "max_celsius": main.get("temp_max"),
        },
        "humidity_percent": main.get("humidity"),
        "pressure_hpa": main.get("pressure"),
        "wind": {
            "speed_mps": wind.get("speed"),
            "degrees": wind.get("deg"),
        },
        "cloudiness_percent": clouds.get("all"),
    }


def compact_forecast_data(
    data: dict[str, Any],
    *,
    days: int | None = None,
) -> dict[str, Any]:
    city = _dict_field(data, "city")
    timezone_offset = _validated_timezone_offset(city.get("timezone"))
    raw_forecasts = data.get("list", [])
    if not isinstance(raw_forecasts, list):
        raise WeatherNormalizationError("forecast list is not an array")
    forecasts_by_date: dict[str, list[dict[str, Any]]] = {}
    for item in raw_forecasts:
        if not isinstance(item, dict):
            raise WeatherNormalizationError("forecast list contains a non-object item")
        compact_item = _compact_forecast_item(
            item,
            timezone_offset_seconds=timezone_offset,
        )
        local_date = compact_item["local_date"]
        forecasts_by_date.setdefault(local_date, []).append(compact_item)

    for items in forecasts_by_date.values():
        items.sort(key=lambda item: item["timestamp"])

    all_daily = [
        _compact_daily_forecast(date, items)
        for date, items in sorted(forecasts_by_date.items())
    ]
    daily = all_daily[:days] if days is not None else all_daily
    all_intervals = [
        interval
        for day in daily
        for interval in day.get("intervals", [])
        if isinstance(interval, dict)
    ]

    return {
        "location": city.get("name", data.get("location", "")),
        "country": city.get("country", ""),
        "timezone": timezone_offset,
        "timezone_offset_seconds": timezone_offset,
        "requested_days": days,
        "available_day_count": len(daily),
        "interval_count": len(all_intervals),
        "coverage_start_local": (
            all_intervals[0].get("forecast_at_local") if all_intervals else None
        ),
        "coverage_end_local": (
            all_intervals[-1].get("forecast_at_local") if all_intervals else None
        ),
        "source_granularity": "3-hour forecast intervals",
        "day_grouping": "location_local_date",
        "interval_time_basis": "location_local_time",
        "days": daily,
    }


def _compact_forecast_item(
    item: dict[str, Any],
    *,
    timezone_offset_seconds: Any = None,
) -> dict[str, Any]:
    weather_items = item.get("weather")
    first_weather = weather_items[0] if isinstance(weather_items, list) and weather_items else {}
    main = _dict_field(item, "main")
    wind = _dict_field(item, "wind")
    clouds = _dict_field(item, "clouds")
    timestamp = item.get("dt")
    utc_datetime, local_datetime = _forecast_datetimes(
        timestamp,
        timezone_offset_seconds,
    )
    return {
        "timestamp": timestamp,
        "time": local_datetime.strftime("%Y-%m-%d %H:%M:%S"),
        "provider_time_utc": item.get("dt_txt"),
        "forecast_at_utc": utc_datetime.isoformat(),
        "forecast_at_local": local_datetime.isoformat(),
        "local_date": local_datetime.date().isoformat(),
        "condition": {
            "main": first_weather.get("main", ""),
            "description": first_weather.get("description", ""),
        },
        "temperature_celsius": main.get("temp"),
        "feels_like_celsius": main.get("feels_like"),
        "humidity_percent": main.get("humidity"),
        "pressure_hpa": main.get("pressure"),
        "rain_probability": item.get("pop"),
        "rain_3h_mm": _forecast_rain_3h_mm(item),
        "wind_speed_mps": wind.get("speed"),
        "cloudiness_percent": clouds.get("all"),
    }


def _compact_daily_forecast(date: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    temperatures = _number_values(item.get("temperature_celsius") for item in items)
    feels_like_temperatures = _number_values(
        item.get("feels_like_celsius") for item in items
    )
    rain_probabilities = _number_values(item.get("rain_probability") for item in items)
    rain_amounts = _number_values(item.get("rain_3h_mm") for item in items)
    descriptions = [
        str(item.get("condition", {}).get("description", ""))
        for item in items
        if item.get("condition", {}).get("description")
    ]
    representative = max(
        items,
        key=lambda item: (
            _number_or_default(item.get("rain_probability")),
            _number_or_default(item.get("temperature_celsius")),
        ),
        default={},
    )
    return {
        "date": date,
        "day_grouping": "location_local_date",
        "interval_count": len(items),
        "coverage_start_local": items[0].get("forecast_at_local") if items else None,
        "coverage_end_local": items[-1].get("forecast_at_local") if items else None,
        "is_partial_day": len(items) < 8,
        "temperature": {
            "min_celsius": min(temperatures) if temperatures else None,
            "max_celsius": max(temperatures) if temperatures else None,
        },
        "max_rain_probability": max(rain_probabilities) if rain_probabilities else None,
        "total_rain_mm": (
            round(sum(rain_amounts), 2)
            if items and len(rain_amounts) == len(items)
            else None
        ),
        "rain_data_complete": bool(items) and len(rain_amounts) == len(items),
        "common_conditions": sorted(set(descriptions)),
        "condition": representative.get("condition", {}),
        "temperature_feels_like_celsius": (
            max(feels_like_temperatures) if feels_like_temperatures else None
        ),
        "humidity_percent": _average_number(item.get("humidity_percent") for item in items),
        "pressure_hpa": _average_number(item.get("pressure_hpa") for item in items),
        "wind_speed_mps": _average_number(item.get("wind_speed_mps") for item in items),
        "intervals": items,
    }


def _number_values(values: Any) -> list[float]:
    return [
        value
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
    return value if isinstance(value, dict) else {}


def _validated_timezone_offset(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherNormalizationError("city.timezone is missing or invalid")
    numeric_value = float(value)
    if not numeric_value.is_integer() or abs(numeric_value) > 18 * 60 * 60:
        raise WeatherNormalizationError("city.timezone is outside the valid range")
    return int(numeric_value)


def _forecast_datetimes(
    timestamp: Any,
    timezone_offset_seconds: int,
) -> tuple[datetime, datetime]:
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise WeatherNormalizationError("forecast interval dt is missing or invalid")
    try:
        utc_datetime = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        local_timezone = timezone(timedelta(seconds=timezone_offset_seconds))
        local_datetime = utc_datetime.astimezone(local_timezone)
    except (OverflowError, OSError, ValueError) as exc:
        raise WeatherNormalizationError(
            "forecast interval dt cannot be converted"
        ) from exc
    return utc_datetime, local_datetime


def _forecast_rain_3h_mm(item: dict[str, Any]) -> float | None:
    """Interpret an omitted OpenWeather rain object as zero forecast rainfall."""

    if "rain" not in item or item.get("rain") is None:
        return 0.0
    rain = item.get("rain")
    if not isinstance(rain, dict) or "3h" not in rain:
        return None
    amount = rain.get("3h")
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or amount < 0
    ):
        return None
    return float(amount)


def _timestamp_iso_fields(
    timestamp: Any,
    timezone_offset_seconds: Any,
    *,
    utc_field: str,
    local_field: str,
) -> dict[str, str | None]:
    """Return UTC and location-local ISO timestamps without changing raw values."""

    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return {utc_field: None, local_field: None}
    if isinstance(timezone_offset_seconds, bool) or not isinstance(
        timezone_offset_seconds, (int, float)
    ):
        timezone_offset_seconds = 0

    try:
        utc_datetime = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        local_timezone = timezone(timedelta(seconds=float(timezone_offset_seconds)))
        local_datetime = utc_datetime.astimezone(local_timezone)
    except (OverflowError, OSError, ValueError):
        return {utc_field: None, local_field: None}
    return {
        utc_field: utc_datetime.isoformat(),
        local_field: local_datetime.isoformat(),
    }


def _weather_error(message: str) -> ServiceResponse:
    return {
        "ok": False,
        "error": {
            "source": WEATHER_SOURCE,
            "message": message,
            "status_code": None,
        },
    }


def _print_raw_api_response(
    request_type: str,
    location: str,
    response: ServiceResponse,
) -> None:
    """Print the OpenWeather response before any compaction or normalization."""

    raw_text = response.get("raw_text")
    payload: Any = response.get("data") if response.get("ok") else response.get("error")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    parsed_message = (
        f"[OpenWeather][{request_type}][RAW_API_RESPONSE] "
        f"location={location!r}\n{serialized}"
    )
    if raw_text is None:
        message = parsed_message
    else:
        message = (
            f"[OpenWeather][{request_type}][RAW_RESPONSE_TEXT] "
            f"location={location!r}\n{raw_text}\n{parsed_message}"
        )
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            raise
        buffer.write((message + "\n").encode("utf-8"))
        buffer.flush()
