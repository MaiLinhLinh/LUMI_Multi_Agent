import pytest

from rag_manager.visualization.validator import (
    VisualizationValidationError,
    validate_fill_plan,
    validate_placeholders,
    validate_security,
    validate_template_syntax,
)


def _valid_fill_plan() -> dict:
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


def _available_fields() -> list[str]:
    return [
        "location.name",
        "current.temperature.current_celsius",
        "current.humidity_percent",
        "current.wind.speed_mps",
        "current.pressure_hpa",
        "forecast.days[].date",
    ]


def test_validate_fill_plan_accepts_valid_plan() -> None:
    normalized = validate_fill_plan(_valid_fill_plan(), available_fields=_available_fields())

    assert normalized["base_template"] == "base_dashboard"
    assert normalized["slots"]["hero"] == ["temperature_hero"]


def test_validate_fill_plan_rejects_unknown_component() -> None:
    plan = _valid_fill_plan()
    plan["slots"]["hero"] = ["missing_component"]

    with pytest.raises(VisualizationValidationError, match="Unknown visualization component"):
        validate_fill_plan(plan, available_fields=_available_fields())


def test_validate_fill_plan_rejects_hidden_component() -> None:
    with pytest.raises(VisualizationValidationError, match="fields are missing"):
        validate_fill_plan(_valid_fill_plan(), available_fields=["location.name"])


def test_validate_fill_plan_rejects_invalid_slot() -> None:
    plan = _valid_fill_plan()
    plan["slots"]["unknown"] = ["source_note"]

    with pytest.raises(VisualizationValidationError, match="Unknown slot"):
        validate_fill_plan(plan, available_fields=_available_fields())


def test_validate_fill_plan_rejects_component_in_wrong_slot() -> None:
    plan = _valid_fill_plan()
    plan["slots"]["hero"] = ["metric_card"]

    with pytest.raises(VisualizationValidationError, match="does not support slot"):
        validate_fill_plan(plan, available_fields=_available_fields())


def test_validate_template_syntax_rejects_unfilled_slot() -> None:
    with pytest.raises(VisualizationValidationError, match="slot placeholders"):
        validate_template_syntax("<section>{{ slot.hero }}</section>")


def test_validate_placeholders_rejects_unknown_placeholder() -> None:
    with pytest.raises(VisualizationValidationError, match="Unsupported template placeholder"):
        validate_placeholders("<div>{{ unsafe.value }}</div>")


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script>",
        "<iframe src='x'></iframe>",
        "<form action='https://example.com'></form>",
        "<button onclick='x()'>Run</button>",
        "<img src='https://example.com/a.png'>",
        "<div>fetch('/data')</div>",
    ],
)
def test_validate_security_rejects_unsafe_html(html: str) -> None:
    with pytest.raises(VisualizationValidationError):
        validate_security(html)


def test_validate_security_accepts_static_html() -> None:
    validate_security("<section><h1>{{ data.location.name }}</h1></section>")

