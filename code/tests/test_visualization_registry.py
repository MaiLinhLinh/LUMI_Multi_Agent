import pytest

from rag_manager.visualization.registry import (
    TemplateRegistryError,
    list_templates,
    lookup_template,
    read_template_metadata,
    recommend_templates,
)


def test_lookup_template_resolves_weather_basic() -> None:
    asset = lookup_template("weather_basic")

    assert asset.template_id == "weather_basic"
    assert asset.template_path.name == "template.html"
    assert asset.metadata["domain"] == "weather"


def test_lookup_template_rejects_unknown_id() -> None:
    with pytest.raises(TemplateRegistryError):
        lookup_template("missing_template")


def test_lookup_template_rejects_path_like_id() -> None:
    with pytest.raises(TemplateRegistryError):
        lookup_template("../weather_basic")


def test_read_template_metadata_returns_copy() -> None:
    metadata = read_template_metadata("weather_basic")
    metadata["id"] = "changed"

    assert read_template_metadata("weather_basic")["id"] == "weather_basic"


def test_list_templates_filters_by_domain() -> None:
    weather_templates = list_templates(domain="weather")

    assert [template["id"] for template in weather_templates] == [
        "weather_basic",
        "weather_forecast",
    ]
    assert list_templates(domain="news") == []


def test_recommend_templates_ranks_compatible_weather_template() -> None:
    recommendations = recommend_templates(
        domain="weather",
        schema_version="weather.combined.v1",
        available_fields=[
            "location.name",
            "current.temperature.current_celsius",
            "forecast.days[].intervals[].time",
        ],
    )

    assert recommendations[0]["id"] == "weather_basic"
    assert recommendations[0]["score"] > 100
    assert "current.temperature.current_celsius" in recommendations[0]["required_fields"]


def test_recommend_templates_skips_missing_required_fields() -> None:
    assert (
        recommend_templates(
            domain="weather",
            schema_version="weather.current.v1",
            available_fields=[],
        )
        == []
    )


def test_recommend_templates_skips_incompatible_schema() -> None:
    assert (
        recommend_templates(
            domain="weather",
            schema_version="weather.error.v1",
            available_fields=["location.name"],
        )
        == []
    )
