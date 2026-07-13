"""OpenWeatherMap service client."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from rag_manager.services.http_client import ServiceResponse, get_json


OPENWEATHER_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
OPENWEATHER_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
WEATHER_SOURCE = "weather"


def fetch_weather(
    location: str,
    *,
    api_key: str,
    timeout_seconds: float = 8,
) -> ServiceResponse:
    if not api_key.strip():
        return _weather_error("Missing OPENWEATHER_API_KEY.")
    if not location.strip():
        return _weather_error("Missing weather location.")

    response = get_json(
        OPENWEATHER_CURRENT_URL,
        source=WEATHER_SOURCE,
        params={
            "q": location,
            "appid": api_key,
            "units": "metric",
            "lang": "vi",
        },
        timeout_seconds=timeout_seconds,
    )
    _print_raw_api_response("current", location, response)
    if not response.get("ok"):
        return response
    return {"ok": True, "data": compact_weather_data(response["data"])}


def fetch_weather_forecast(
    location: str,
    *,
    api_key: str,
    timeout_seconds: float = 8,
    days: int = 3,
) -> ServiceResponse:
    if not api_key.strip():
        return _weather_error("Missing OPENWEATHER_API_KEY.")
    if not location.strip():
        return _weather_error("Missing weather location.")

    bounded_days = max(1, min(days, 5))
    response = get_json(
        OPENWEATHER_FORECAST_URL,
        source=WEATHER_SOURCE,
        params={
            "q": location,
            "appid": api_key,
            "units": "metric",
            "lang": "vi",
            "cnt": bounded_days * 8,
        },
        timeout_seconds=timeout_seconds,
    )
    _print_raw_api_response("forecast", location, response)
    if not response.get("ok"):
        return response
    return {"ok": True, "data": compact_forecast_data(response["data"], days=bounded_days)}


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


def compact_forecast_data(data: dict[str, Any], *, days: int) -> dict[str, Any]:
    city = _dict_field(data, "city")
    timezone_offset = city.get("timezone")
    forecasts_by_date: dict[str, list[dict[str, Any]]] = {}
    for item in data.get("list", []):
        if not isinstance(item, dict):
            continue
        dt_txt = str(item.get("dt_txt", ""))
        date = dt_txt[:10] if len(dt_txt) >= 10 else str(item.get("dt", ""))
        forecasts_by_date.setdefault(date, []).append(
            _compact_forecast_item(item, timezone_offset_seconds=timezone_offset)
        )

    daily = [
        _compact_daily_forecast(date, items)
        for date, items in list(forecasts_by_date.items())[:days]
    ]

    return {
        "location": city.get("name", data.get("location", "")),
        "country": city.get("country", ""),
        "timezone": timezone_offset,
        "timezone_offset_seconds": timezone_offset,
        "requested_days": days,
        "source_granularity": "3-hour forecast intervals",
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
    rain = _dict_field(item, "rain")
    clouds = _dict_field(item, "clouds")
    timestamp = item.get("dt")
    return {
        "timestamp": timestamp,
        "time": item.get("dt_txt"),
        **_timestamp_iso_fields(
            timestamp,
            timezone_offset_seconds,
            utc_field="forecast_at_utc",
            local_field="forecast_at_local",
        ),
        "condition": {
            "main": first_weather.get("main", ""),
            "description": first_weather.get("description", ""),
        },
        "temperature_celsius": main.get("temp"),
        "feels_like_celsius": main.get("feels_like"),
        "humidity_percent": main.get("humidity"),
        "pressure_hpa": main.get("pressure"),
        "rain_probability": item.get("pop"),
        "rain_3h_mm": rain.get("3h"),
        "wind_speed_mps": wind.get("speed"),
        "cloudiness_percent": clouds.get("all"),
    }


def _compact_daily_forecast(date: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    temperatures = _number_values(item.get("temperature_celsius") for item in items)
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
        "temperature": {
            "min_celsius": min(temperatures) if temperatures else None,
            "max_celsius": max(temperatures) if temperatures else None,
        },
        "max_rain_probability": max(rain_probabilities) if rain_probabilities else None,
        "total_rain_mm": round(sum(rain_amounts), 2) if rain_amounts else None,
        "common_conditions": sorted(set(descriptions)),
        "condition": representative.get("condition", {}),
        "temperature_feels_like_celsius": representative.get("feels_like_celsius"),
        "humidity_percent": _average_number(item.get("humidity_percent") for item in items),
        "pressure_hpa": _average_number(item.get("pressure_hpa") for item in items),
        "wind_speed_mps": _average_number(item.get("wind_speed_mps") for item in items),
        "intervals": items,
    }


def _number_values(values: Any) -> list[float]:
    return [value for value in values if isinstance(value, (int, float))]


def _number_or_default(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else -1.0


def _average_number(values: Any) -> float | None:
    numbers = _number_values(values)
    return round(sum(numbers) / len(numbers), 2) if numbers else None


def _dict_field(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


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
