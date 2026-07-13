import json
from pathlib import Path

import pytest

from rag_manager.visualization.assembler import assemble_existing_template
from rag_manager.visualization.execution_plan import (
    ExecutionPlanError,
    build_runtime_assembly_input,
    validate_execution_plan,
)
from rag_manager.visualization.orchestrator import VisualizationRequest
from rag_manager.visualization.paths import resolve_asset_path
from rag_manager.visualization.template_agent import TemplateAgentWorkflow
from rag_manager.visualization.validator import canonicalize_color, validate_color_value


def _sample() -> dict:
    path = resolve_asset_path("schemas", "weather.combined.v1", "sample.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_existing_execution_plan_requires_template_and_preservation() -> None:
    plan = {
        "plan_version": "1.0",
        "target": {
            "mode": "existing_template",
            "template_ref": {"ref_type": "registry", "id": "weather_basic", "kind": "complete_template"},
            "base_ref": None,
            "preserve_existing_structure": True,
        },
        "lookup_plan": {"templates": [], "base_templates": [], "components": []},
        "resource_plan": {"reuse_components": []},
        "generation_plan": {"base": None, "components": []},
        "modification_plan": {"style": {"background_color": "pink"}, "content": [], "layout": []},
        "todo_list": [],
    }
    assert validate_execution_plan(plan)["target"]["mode"] == "existing_template"

    invalid = {**plan, "target": {**plan["target"], "preserve_existing_structure": False}}
    with pytest.raises(ExecutionPlanError):
        validate_execution_plan(invalid)


def test_existing_template_patch_preserves_rendered_text_and_adds_component() -> None:
    result = TemplateAgentWorkflow().run(
        VisualizationRequest(
            mode="design",
            action="design_template",
            source_template_id="weather_basic",
            user_request="keep current template and add rain icon",
            domain_result={"weather_data": _sample()},
        )
    )
    assert result["ok"] is True
    assert "Ha Noi" in result["html"]
    assert "generated-rain-icon" in result["html"]


def test_runtime_assembly_input_rejects_unvalidated_artifact() -> None:
    plan = {
        "plan_version": "1.0",
        "target": {
            "mode": "base_template",
            "template_ref": None,
            "base_ref": {"ref_type": "registry", "id": "base_dashboard", "kind": "base_template"},
            "preserve_existing_structure": False,
        },
        "lookup_plan": {"templates": [], "base_templates": [], "components": []},
        "resource_plan": {"reuse_components": []},
        "generation_plan": {"base": None, "components": []},
        "modification_plan": {"style": {}, "content": [], "layout": []},
        "todo_list": [],
    }
    with pytest.raises(ExecutionPlanError):
        build_runtime_assembly_input(
            plan,
            generated_component_refs=[
                {"ref_type": "artifact", "artifact_id": "art_bad", "kind": "component", "status": "staged"}
            ],
        )


@pytest.mark.parametrize(
    "value",
    ["light pink", "#ffd1e3", "#fff0f680", "rgb(255, 182, 193)", "rgba(255, 182, 193, 0.6)", "hsl(350, 100%, 88%)"],
)
def test_style_accepts_safe_color_formats(value: str) -> None:
    assert validate_color_value(value) is True
    assert "url(" not in canonicalize_color(value)


def test_style_rejects_css_injection() -> None:
    assert validate_color_value("red; color: blue") is False
    assert validate_color_value("url(https://example.com/x)") is False
