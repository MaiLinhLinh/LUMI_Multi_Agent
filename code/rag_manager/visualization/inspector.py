"""Inspect structured agent data before visualization rendering."""

from __future__ import annotations

from typing import Any


def inspect_actual_data(agent_data: dict[str, Any] | None) -> dict[str, Any]:
    """Return a compact summary of structured visualization-ready data."""

    envelope = _weather_envelope(agent_data)
    data = _dict_or_empty(envelope.get("data"))
    current = data.get("current")
    forecast = data.get("forecast")

    return {
        "domain": _string_value(envelope.get("domain")),
        "schema_version": _string_value(envelope.get("schema_version")),
        "data_type": _string_value(envelope.get("data_type")),
        "location": _string_value(envelope.get("location")),
        "available_fields": _string_list(envelope.get("available_fields")),
        "has_current": isinstance(current, dict) and bool(current),
        "has_forecast": isinstance(forecast, dict) and bool(forecast),
        "source": _dict_or_empty(envelope.get("source")),
        "errors": _dict_list(envelope.get("errors")),
    }


def _weather_envelope(agent_data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(agent_data, dict):
        return {}
    weather_data = agent_data.get("weather_data")
    if isinstance(weather_data, dict):
        return weather_data
    return agent_data


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

