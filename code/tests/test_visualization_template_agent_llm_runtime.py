import json
import pytest
from pathlib import Path

from rag_manager.visualization.llm_output import parse_llm_json_response
from rag_manager.visualization.orchestrator import VisualizationRequest
from rag_manager.visualization.paths import resolve_asset_path
from rag_manager.visualization.prompt_loader import load_template_agent_prompt, render_prompt
from rag_manager.visualization.template_agent import TemplateAgentWorkflow


class FakeTextLlm:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def chat_text(self, *, system_prompt: str, user_message: str, temperature: float = 0.0) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "temperature": temperature,
            }
        )
        return self.responses.pop(0)


def _load_sample(schema_version: str) -> dict:
    sample_path = resolve_asset_path("schemas", schema_version, "sample.json")
    return json.loads(sample_path.read_text(encoding="utf-8"))


def _json(data: dict) -> str:
    return json.dumps(data)


def _valid_llm_responses() -> list[str]:
    return [
        _json(
            {
                "user_goal": "custom weather dashboard",
                "domain": "weather",
                "mode": "customize",
                "style_preferences": ["clean"],
                "data_focus": ["temperature", "forecast"],
            }
        ),
        _json(
            {
                "strategy": "assemble_base",
                "base_template": "base_dashboard",
                "reason": "User asked for customization.",
            }
        ),
        _json(
            {
                "slots": {
                    "hero": ["temperature_hero"],
                    "metrics": ["metric_card"],
                    "chart": ["forecast_chart"],
                    "footer": ["source_note"],
                }
            }
        ),
        _json({"todo_list": ["Assemble visible weather components."]}),
        _json(
            {
                "base_template": "base_dashboard",
                "parameters": {"page_title": "Custom Weather"},
                "slots": {
                    "hero": ["temperature_hero"],
                    "metrics": ["metric_card"],
                    "chart": ["forecast_chart"],
                    "footer": ["source_note"],
                },
            }
        ),
    ]


def test_template_agent_prompt_loader_reads_and_renders_prompt() -> None:
    prompt = load_template_agent_prompt("extract_requirements")
    rendered = render_prompt(
        prompt,
        {
            "user_request": "make dashboard",
            "inspection_json": {"domain": "weather"},
        },
    )

    assert "make dashboard" in rendered
    assert '"domain": "weather"' in rendered


def test_llm1_interprets_template_selection_and_modification() -> None:
    pytest.skip("Legacy template intent parsing moved to Semantic Router.")
    fake_llm = FakeTextLlm(
        [
            _json(
                {
                    "intent": "template",
                    "action": "select_and_customize_template",
                    "selection": {"index": 2, "template_id": None},
                    "modifications": {"style": {"background_color": "pink"}},
                    "status": "ready",
                    "clarifying_question": None,
                }
            )
        ]
    )

    workflow = TemplateAgentWorkflow(llm=fake_llm)
    result = workflow.llm_interpret_template_action(
        request=VisualizationRequest(
            mode="choose",
            user_request="mẫu 2, đổi nền màu hồng",
        ),
        available_templates=[
            {"id": "weather_basic", "type": "existing_template"},
            {"id": "weather_forecast", "type": "existing_template"},
            {"id": "__create_new_template__", "type": "action"},
        ],
    )

    assert result["action"] == "select_and_customize_template"
    assert result["selection"]["index"] == 2
    assert result["modifications"]["style"]["background_color"] == "pink"


def test_parse_llm_json_response_accepts_fenced_json() -> None:
    parsed = parse_llm_json_response('```json\n{"ok": true}\n```')

    assert parsed == {"ok": True}


def test_template_agent_runtime_uses_fake_llm_and_saves_artifact(tmp_path: Path) -> None:
    fake_llm = FakeTextLlm(_valid_llm_responses())

    result = TemplateAgentWorkflow(llm=fake_llm).run(
        VisualizationRequest(
            mode="customize",
            user_request="make a custom dashboard",
            domain_result={"weather_data": _load_sample("weather.combined.v1")},
            output_dir=tmp_path,
        )
    )

    assert result["ok"] is True
    assert result["message"] == "Generated template artifact."
    assert Path(result["template_path"]).exists()
    assert Path(result["html_path"]).exists()
    assert len(fake_llm.calls) == 5
    assert "strategy selector" in fake_llm.calls[1]["user_message"]
    assert "Custom Weather" in result["html"]


def test_template_agent_runtime_rejects_invalid_json(tmp_path: Path) -> None:
    fake_llm = FakeTextLlm(["not json"])

    result = TemplateAgentWorkflow(llm=fake_llm).run(
        VisualizationRequest(
            mode="customize",
            user_request="customize",
            domain_result={"weather_data": _load_sample("weather.combined.v1")},
            output_dir=tmp_path,
        )
    )

    assert result["ok"] is False
    assert result["errors"] == ["template_agent_validation_failed"]
    assert "Invalid LLM JSON" in result["message"]


def test_template_agent_runtime_rejects_invalid_candidate_id(tmp_path: Path) -> None:
    fake_llm = FakeTextLlm(
        [
            _valid_llm_responses()[0],
            _json(
                {
                    "strategy": "assemble_base",
                    "base_template": "made_up_base",
                    "reason": "bad id",
                }
            ),
        ]
    )

    result = TemplateAgentWorkflow(llm=fake_llm).run(
        VisualizationRequest(
            mode="customize",
            user_request="customize",
            domain_result={"weather_data": _load_sample("weather.combined.v1")},
            output_dir=tmp_path,
        )
    )

    assert result["ok"] is False
    assert result["errors"] == ["template_agent_validation_failed"]
    assert "Invalid base_template" in result["message"]


def test_template_agent_runtime_repair_loop_fixes_invalid_fill_plan(tmp_path: Path) -> None:
    responses = _valid_llm_responses()
    responses[-1] = _json(
        {
            "base_template": "base_dashboard",
            "parameters": {"page_title": "Broken Weather"},
            "slots": {"hero": ["metric_card"]},
        }
    )
    responses.append(
        _json(
            {
                "base_template": "base_dashboard",
                "parameters": {"page_title": "Repaired Weather"},
                "slots": {
                    "hero": ["temperature_hero"],
                    "metrics": ["metric_card"],
                    "chart": ["forecast_chart"],
                    "footer": ["source_note"],
                },
            }
        )
    )
    fake_llm = FakeTextLlm(responses)

    result = TemplateAgentWorkflow(llm=fake_llm, max_repair_attempts=1).run(
        VisualizationRequest(
            mode="customize",
            user_request="customize",
            domain_result={"weather_data": _load_sample("weather.combined.v1")},
            output_dir=tmp_path,
        )
    )

    assert result["ok"] is True
    assert len(fake_llm.calls) == 6
    assert "fill-plan repairer" in fake_llm.calls[-1]["user_message"]
    assert "Repaired Weather" in result["html"]
