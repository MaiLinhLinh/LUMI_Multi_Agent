import json

from rag_manager.visualization.orchestrator import (
    VisualizationOrchestrator,
    VisualizationRequest,
    VisualizationResult,
)
from rag_manager.visualization.paths import resolve_asset_path


def _load_sample(schema_version: str) -> dict:
    sample_path = resolve_asset_path("schemas", schema_version, "sample.json")
    return json.loads(sample_path.read_text(encoding="utf-8"))


def test_orchestrator_renders_explicit_existing_template(tmp_path) -> None:
    envelope = _load_sample("weather.combined.v1")
    orchestrator = VisualizationOrchestrator()

    result = orchestrator.run(
        VisualizationRequest(
            domain_result={
                "weather_answer": "Weather answer.",
                "weather_data": envelope,
            },
            mode="choose",
            template_id="weather_basic",
            output_dir=tmp_path,
        )
    )

    assert result.ok is True
    assert result.template_id == "weather_basic"
    assert result.html_path is not None
    assert "Ha Noi" in result.html
    assert "Weather answer." in result.html
    assert "12:00" in result.html


def test_orchestrator_auto_recommends_compatible_template(tmp_path) -> None:
    envelope = _load_sample("weather.current.v1")
    orchestrator = VisualizationOrchestrator()

    result = orchestrator.run(
        {
            "domain_result": {
                "answer": "Auto weather answer.",
                "weather_data": envelope,
            },
            "mode": "auto",
            "output_dir": tmp_path,
        }
    )

    assert result.ok is True
    assert result.template_id == "weather_basic"
    assert result.available_templates[0]["id"] == "weather_basic"
    assert result.html_path is not None


def test_orchestrator_uses_forecast_template_for_forecast_only_data(tmp_path) -> None:
    envelope = _load_sample("weather.forecast.v1")
    result = VisualizationOrchestrator().run(
        VisualizationRequest(
            domain_result={"weather_data": envelope, "answer": "Forecast answer."},
            mode="auto",
            output_dir=tmp_path,
        )
    )

    assert result.ok is True
    assert result.template_id == "weather_forecast"
    assert "31" not in result.html
    assert "12:00" in result.html
    assert "°C" in result.html


def test_orchestrator_uses_current_card_for_one_hour_forecast(tmp_path) -> None:
    envelope = _load_sample("weather.combined.v1")
    envelope["data"]["presentation"] = {
        "mode": "hourly_forecast",
        "time_label": "Dự báo lúc 09:00 ngày 17/07/2026",
        "interval_notice": "",
    }
    envelope["available_fields"].extend(
        [
            "presentation.mode",
            "presentation.time_label",
        ]
    )

    result = VisualizationOrchestrator().run(
        VisualizationRequest(
            domain_result={
                "weather_data": envelope,
                "weather_answer": "Dự báo thời tiết theo giờ.",
            },
            mode="auto",
            output_dir=tmp_path,
        )
    )

    assert result.ok is True
    assert result.template_id == "weather_basic"
    assert "Dự báo lúc 09:00 ngày 17/07/2026" in result.html
    assert "Forecast data is not available." not in result.html
    assert "<h2>Forecast</h2>" not in result.html


def test_orchestrator_lists_templates_for_choose_mode_without_id(tmp_path) -> None:
    envelope = _load_sample("weather.current.v1")
    result = VisualizationOrchestrator().run(
        VisualizationRequest(
            domain_result={"weather_data": envelope},
            mode="choose",
            output_dir=tmp_path,
        )
    )

    assert result.ok is False
    assert result.errors == ["template_selection_required"]
    assert result.html_path is None
    assert result.available_templates
    assert result.available_templates[-1]["type"] == "create_new_template"
    assert "Tạo template mới" in result.message
    assert "chọn mẫu" in result.message


def test_orchestrator_returns_controlled_error_when_missing_domain_result() -> None:
    result = VisualizationOrchestrator().run(VisualizationRequest())

    assert result.ok is False
    assert result.errors == ["missing_domain_result"]
    assert "du lieu domain" in result.message


def test_orchestrator_returns_controlled_error_for_unknown_template(tmp_path) -> None:
    envelope = _load_sample("weather.current.v1")

    result = VisualizationOrchestrator().run(
        VisualizationRequest(
            domain_result={"weather_data": envelope},
            mode="choose",
            template_id="missing_template",
            output_dir=tmp_path,
        )
    )

    assert result.ok is False
    assert result.errors == ["template_lookup_failed"]
    assert "Unknown visualization template" in result.message


def test_orchestrator_does_not_render_when_no_compatible_template(tmp_path) -> None:
    envelope = _load_sample("weather.error.v1")

    result = VisualizationOrchestrator().run(
        VisualizationRequest(
            domain_result={"weather_data": envelope},
            mode="auto",
            output_dir=tmp_path,
        )
    )

    assert result.ok is False
    assert result.errors == ["no_compatible_template"]
    assert result.html_path is None


def test_create_mode_calls_template_agent_workflow() -> None:
    class FakeWorkflow:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request):
            self.requests.append(request)
            return VisualizationResult(
                ok=True,
                mode=request.mode,
                template_id="generated_weather",
                message="Generated.",
            )

    workflow = FakeWorkflow()
    request = VisualizationRequest(mode="create", user_request="make it blue")
    result = VisualizationOrchestrator(template_agent_workflow=workflow).run(request)

    assert workflow.requests == [request]
    assert result.ok is True
    assert result.mode == "create"
    assert result.template_id == "generated_weather"
