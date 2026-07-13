import json

import pytest

from rag_manager.visualization.assembler import assemble_template_from_base
from rag_manager.visualization.paths import resolve_asset_path
from rag_manager.visualization.renderer import render_template
from rag_manager.visualization.validator import VisualizationValidationError


def _load_sample(schema_version: str) -> dict:
    sample_path = resolve_asset_path("schemas", schema_version, "sample.json")
    return json.loads(sample_path.read_text(encoding="utf-8"))


def _fill_plan() -> dict:
    return {
        "base_template": "base_dashboard",
        "parameters": {"page_title": "Ha Noi Weather"},
        "slots": {
            "hero": ["temperature_hero"],
            "metrics": ["metric_card"],
            "chart": ["forecast_chart"],
            "footer": ["source_note"],
        },
    }


def test_assemble_template_from_base_inserts_components() -> None:
    envelope = _load_sample("weather.combined.v1")

    html = assemble_template_from_base(
        _fill_plan(),
        available_fields=envelope["available_fields"],
    )

    assert "<title>Ha Noi Weather</title>" in html
    assert "temperature-hero" in html
    assert "metric-card" in html
    assert "forecast-chart" in html
    assert "{{ slot." not in html


def test_assembled_template_can_render_with_sample_data() -> None:
    envelope = _load_sample("weather.combined.v1")
    template_html = assemble_template_from_base(
        _fill_plan(),
        available_fields=envelope["available_fields"],
    )

    rendered = render_template(
        template_html,
        answer="Weather answer.",
        data=envelope["data"] | {"source": envelope["source"]},
    )

    assert "Ha Noi" in rendered
    assert "30" in rendered
    assert "12:00" in rendered
    assert "None" not in rendered
    assert "undefined" not in rendered


def test_assemble_template_rejects_component_hidden_by_missing_fields() -> None:
    with pytest.raises(VisualizationValidationError, match="fields are missing"):
        assemble_template_from_base(_fill_plan(), available_fields=["location.name"])


def test_assemble_template_rejects_invalid_component_id() -> None:
    plan = _fill_plan()
    plan["slots"]["hero"] = ["missing_component"]

    with pytest.raises(VisualizationValidationError, match="Unknown visualization component"):
        assemble_template_from_base(plan, available_fields=["location.name"])

