"""OpenWeatherMap service client."""

from __future__ import annotations

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


def compact_forecast_data(data: dict[str, Any], *, days: int) -> dict[str, Any]:
    city = _dict_field(data, "city")
    forecasts_by_date: dict[str, list[dict[str, Any]]] = {}
    for item in data.get("list", []):
        if not isinstance(item, dict):
            continue
        dt_txt = str(item.get("dt_txt", ""))
        date = dt_txt[:10] if len(dt_txt) >= 10 else str(item.get("dt", ""))
        forecasts_by_date.setdefault(date, []).append(_compact_forecast_item(item))

    daily = [
        _compact_daily_forecast(date, items)
        for date, items in list(forecasts_by_date.items())[:days]
    ]

    return {
        "location": city.get("name", data.get("location", "")),
        "country": city.get("country", ""),
        "timezone": city.get("timezone"),
        "requested_days": days,
        "source_granularity": "3-hour forecast intervals",
        "days": daily,
    }


def _compact_forecast_item(item: dict[str, Any]) -> dict[str, Any]:
    weather_items = item.get("weather")
    first_weather = weather_items[0] if isinstance(weather_items, list) and weather_items else {}
    main = _dict_field(item, "main")
    wind = _dict_field(item, "wind")
    rain = _dict_field(item, "rain")
    clouds = _dict_field(item, "clouds")
    return {
        "timestamp": item.get("dt"),
        "time": item.get("dt_txt"),
        "condition": {
            "main": first_weather.get("main", ""),
            "description": first_weather.get("description", ""),
        },
        "temperature_celsius": main.get("temp"),
        "feels_like_celsius": main.get("feels_like"),
        "humidity_percent": main.get("humidity"),
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
    return {
        "date": date,
        "temperature": {
            "min_celsius": min(temperatures) if temperatures else None,
            "max_celsius": max(temperatures) if temperatures else None,
        },
        "max_rain_probability": max(rain_probabilities) if rain_probabilities else None,
        "total_rain_mm": round(sum(rain_amounts), 2) if rain_amounts else None,
        "common_conditions": sorted(set(descriptions)),
        "intervals": items,
    }


def _number_values(values: Any) -> list[float]:
    return [value for value in values if isinstance(value, (int, float))]


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
