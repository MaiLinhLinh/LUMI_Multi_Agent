import json

import pytest

from rag_manager.visualization.components import (
    ComponentRegistryError,
    filter_visible_components,
    list_components,
    read_component,
    search_components_by_metadata,
)
from rag_manager.visualization.paths import resolve_asset_path


def test_base_dashboard_assets_are_valid() -> None:
    base_path = resolve_asset_path("base_templates", "base_dashboard", "base.html")
    metadata_path = resolve_asset_path("base_templates", "base_dashboard", "metadata.json")
    contract_path = resolve_asset_path("base_templates", "base_dashboard", "contract.json")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    assert base_path.exists()
    assert metadata["id"] == "base_dashboard"
    assert metadata["slots"] == ["hero", "metrics", "chart", "footer"]
    assert contract["missing_field_policy"] == "hide_component"
    assert "hero" in contract["slots"]


def test_list_components_returns_weather_components() -> None:
    components = list_components({"domain": "weather"})

    assert [component["id"] for component in components] == [
        "forecast_chart",
        "metric_card",
        "source_note",
        "temperature_hero",
    ]


def test_read_component_returns_html_and_metadata() -> None:
    component = read_component("temperature_hero")

    assert component.component_id == "temperature_hero"
    assert "{{ data.current.temperature.current_celsius }}" in component.html
    assert component.metadata["supported_slots"] == ["hero"]


def test_read_component_rejects_unknown_or_path_like_id() -> None:
    with pytest.raises(ComponentRegistryError):
        read_component("missing_component")
    with pytest.raises(ComponentRegistryError):
        read_component("../temperature_hero")


def test_search_components_by_metadata_filters_domain_slot_and_tags() -> None:
    chart_components = search_components_by_metadata(
        domain="weather",
        slot="chart",
        tags=["forecast"],
    )

    assert [component["id"] for component in chart_components] == ["forecast_chart"]


def test_filter_visible_components_uses_required_fields() -> None:
    components = list_components({"domain": "weather"})
    filtered = filter_visible_components(
        components,
        available_fields=[
            "location.name",
            "current.temperature.current_celsius",
            "current.humidity_percent",
            "current.wind.speed_mps",
            "current.pressure_hpa",
        ],
    )

    visible_ids = [component["id"] for component in filtered["visible_components"]]
    hidden = {component["id"]: component for component in filtered["hidden_components"]}

    assert visible_ids == ["metric_card", "source_note", "temperature_hero"]
    assert hidden["forecast_chart"]["missing_fields"] == ["forecast.days[].date"]


def test_filter_visible_components_allows_forecast_when_required_field_exists() -> None:
    components = search_components_by_metadata(domain="weather", slot="chart")
    filtered = filter_visible_components(
        components,
        available_fields=["forecast.days[].date"],
    )

    assert [component["id"] for component in filtered["visible_components"]] == [
        "forecast_chart"
    ]
    assert filtered["hidden_components"] == []

