"""OpenWeatherMap service client."""

from __future__ import annotations

from typing import Any

from rag_manager.services.http_client import ServiceResponse, get_json


OPENWEATHER_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
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
    if not response.get("ok"):
        return response
    return {"ok": True, "data": compact_weather_data(response["data"])}


def compact_weather_data(data: dict[str, Any]) -> dict[str, Any]:
    weather_items = data.get("weather")
    first_weather = weather_items[0] if isinstance(weather_items, list) and weather_items else {}
    main = _dict_field(data, "main")
    wind = _dict_field(data, "wind")
    clouds = _dict_field(data, "clouds")
    sys = _dict_field(data, "sys")

    return {
        "location": data.get("name", ""),
        "country": sys.get("country", ""),
        "timestamp": data.get("dt"),
        "timezone": data.get("timezone"),
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


def _dict_field(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _weather_error(message: str) -> ServiceResponse:
    return {
        "ok": False,
        "error": {
            "source": WEATHER_SOURCE,
            "message": message,
            "status_code": None,
        },
    }
