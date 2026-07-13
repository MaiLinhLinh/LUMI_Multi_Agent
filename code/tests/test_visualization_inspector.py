import json

from rag_manager.visualization.inspector import inspect_actual_data
from rag_manager.visualization.paths import resolve_asset_path


def _load_sample(schema_version: str) -> dict:
    sample_path = resolve_asset_path("schemas", schema_version, "sample.json")
    return json.loads(sample_path.read_text(encoding="utf-8"))


def test_inspector_reads_current_weather_envelope() -> None:
    summary = inspect_actual_data(_load_sample("weather.current.v1"))

    assert summary["domain"] == "weather"
    assert summary["schema_version"] == "weather.current.v1"
    assert summary["data_type"] == "current"
    assert summary["location"] == "Ha Noi"
    assert summary["has_current"] is True
    assert summary["has_forecast"] is False
    assert "current.temperature.current_celsius" in summary["available_fields"]


def test_inspector_reads_forecast_weather_envelope() -> None:
    summary = inspect_actual_data(_load_sample("weather.forecast.v1"))

    assert summary["data_type"] == "forecast"
    assert summary["has_current"] is False
    assert summary["has_forecast"] is True
    assert "forecast.days[].intervals[].time" in summary["available_fields"]


def test_inspector_reads_combined_weather_envelope() -> None:
    summary = inspect_actual_data(_load_sample("weather.combined.v1"))

    assert summary["schema_version"] == "weather.combined.v1"
    assert summary["has_current"] is True
    assert summary["has_forecast"] is True


def test_inspector_preserves_error_metadata() -> None:
    summary = inspect_actual_data(_load_sample("weather.error.v1"))

    assert summary["data_type"] == "error"
    assert summary["has_current"] is False
    assert summary["has_forecast"] is False
    assert summary["errors"][0]["message"] == "Missing OPENWEATHER_API_KEY."


def test_inspector_handles_empty_weather_envelope() -> None:
    summary = inspect_actual_data(_load_sample("weather.empty.v1"))

    assert summary["data_type"] == "empty"
    assert summary["available_fields"] == []
    assert summary["has_current"] is False
    assert summary["has_forecast"] is False


def test_inspector_can_read_weather_data_from_graph_state() -> None:
    summary = inspect_actual_data(
        {
            "weather_answer": "This text must not be parsed.",
            "weather_data": _load_sample("weather.current.v1"),
        }
    )

    assert summary["schema_version"] == "weather.current.v1"
    assert summary["location"] == "Ha Noi"


def test_inspector_missing_fields_are_not_errors() -> None:
    summary = inspect_actual_data({"domain": "weather", "data": {"current": {}}})

    assert summary["domain"] == "weather"
    assert summary["schema_version"] == ""
    assert summary["available_fields"] == []
    assert summary["has_current"] is False
    assert summary["has_forecast"] is False

