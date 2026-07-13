import json
from pathlib import Path

from rag_manager.visualization.orchestrator import VisualizationRequest
from rag_manager.visualization.paths import resolve_asset_path
from rag_manager.visualization.template_agent import (
    TemplateAgentWorkflow,
    search_base_templates_by_metadata,
)


def _load_sample(schema_version: str) -> dict:
    sample_path = resolve_asset_path("schemas", schema_version, "sample.json")
    return json.loads(sample_path.read_text(encoding="utf-8"))


def test_search_base_templates_by_metadata_filters_domain() -> None:
    assert [item["id"] for item in search_base_templates_by_metadata(domain="weather")] == [
        "base_dashboard"
    ]
    assert search_base_templates_by_metadata(domain="news") == []


def test_template_agent_uses_existing_template_when_match_is_high(tmp_path: Path) -> None:
    class TrackingWorkflow(TemplateAgentWorkflow):
        def __init__(self) -> None:
            super().__init__()
            self.fill_plan_calls = 0

        def llm_generate_fill_plan(self, **kwargs):
            self.fill_plan_calls += 1
            return super().llm_generate_fill_plan(**kwargs)

    workflow = TrackingWorkflow()
    result = workflow.run(
        VisualizationRequest(
            mode="create",
            domain_result={"weather_data": _load_sample("weather.current.v1")},
            output_dir=tmp_path,
        )
    )

    assert result["ok"] is True
    assert result["template_id"] == "weather_basic"
    assert result["message"] == "Existing template match selected."
    assert result["template_path"] is None
    assert result["html_path"] is None
    assert workflow.fill_plan_calls == 0


def test_template_agent_assembles_base_template_and_saves_artifacts(tmp_path: Path) -> None:
    result = TemplateAgentWorkflow().run(
        VisualizationRequest(
            mode="customize",
            user_request="make a custom weather dashboard",
            domain_result={
                "weather_answer": "Weather answer.",
                "weather_data": _load_sample("weather.combined.v1"),
            },
            output_dir=tmp_path,
        )
    )

    assert result["ok"] is True
    assert result["message"] == "Generated template artifact."
    assert result["html_path"] is not None
    assert result["template_path"] is not None
    assert "Ha Noi" in result["html"]
    assert "temperature-hero" in result["html"]
    assert "forecast-chart" in result["html"]

    template_path = Path(result["template_path"])
    artifact_dir = template_path.parent
    assert template_path.exists()
    assert (artifact_dir / "metadata.json").exists()
    assert (artifact_dir / "fill_plan.json").exists()

    metadata = json.loads((artifact_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["source"]["base_template"] == "base_dashboard"
    assert metadata["source"]["validation"] == "deterministic"
    assert "temperature_hero" in metadata["source"]["components"]["hero"]


def test_template_agent_rejects_invalid_base_id_from_llm(tmp_path: Path) -> None:
    class BadBaseWorkflow(TemplateAgentWorkflow):
        def llm_decide_template_strategy(self, **kwargs):
            return {"strategy": "assemble_base", "base_template": "made_up_base"}

    result = BadBaseWorkflow().run(
        VisualizationRequest(
            mode="customize",
            user_request="customize",
            domain_result={"weather_data": _load_sample("weather.combined.v1")},
            output_dir=tmp_path,
        )
    )

    assert result["ok"] is False
    assert result["errors"] == ["template_agent_validation_failed"]
    assert "invalid base_template" in result["message"]


def test_template_agent_rejects_invalid_component_id_from_llm(tmp_path: Path) -> None:
    class BadComponentWorkflow(TemplateAgentWorkflow):
        def llm_select_components(self, **kwargs):
            return {"hero": ["made_up_component"]}

    result = BadComponentWorkflow().run(
        VisualizationRequest(
            mode="customize",
            user_request="customize",
            domain_result={"weather_data": _load_sample("weather.combined.v1")},
            output_dir=tmp_path,
        )
    )

    assert result["ok"] is False
    assert result["errors"] == ["template_agent_validation_failed"]
    assert "unknown or hidden component_id" in result["message"]


def test_template_agent_rejects_hidden_component_from_llm(tmp_path: Path) -> None:
    class HiddenComponentWorkflow(TemplateAgentWorkflow):
        def llm_select_components(self, **kwargs):
            return {"chart": ["forecast_chart"]}

    result = HiddenComponentWorkflow().run(
        VisualizationRequest(
            mode="customize",
            user_request="customize",
            domain_result={"weather_data": _load_sample("weather.current.v1")},
            output_dir=tmp_path,
        )
    )

    assert result["ok"] is False
    assert result["errors"] == ["template_agent_validation_failed"]
    assert "unknown or hidden component_id" in result["message"]


def test_bare_create_request_asks_for_requirements_before_using_domain_data() -> None:
    result = TemplateAgentWorkflow().run(
        VisualizationRequest(
            mode="create",
            user_request="toi muon tao template moi",
            domain_result={"weather_data": _load_sample("weather.current.v1")},
        )
    )

    assert result["ok"] is False
    assert result["errors"] == ["missing_template_requirements"]
    assert result["html_path"] is None
    assert result["metadata"]["pending_template_state"]["status"] == "collecting_requirements"


def test_create_request_with_style_requirement_generates_preview_artifact(tmp_path: Path) -> None:
    result = TemplateAgentWorkflow().run(
        VisualizationRequest(
            mode="create",
            user_request="toi theo doi thoi tiet, nen mau trang hong, hien thi gio va nhiet do",
            domain_result={"weather_data": _load_sample("weather.current.v1")},
            output_dir=tmp_path,
        )
    )

    assert result["ok"] is True
    assert result["message"] == "Generated template artifact."
    assert result["html_path"] is not None
    assert "data-generated-style=\"pink-white\"" in result["html"]
