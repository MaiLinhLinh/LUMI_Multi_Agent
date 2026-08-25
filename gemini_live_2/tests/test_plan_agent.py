"""Unit tests for the native Function Calling Plan Agent loop."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from google.genai import types

from gemini_live_2.catalogs.domains import DomainRegistry
from gemini_live_2.gateway import CapabilityDescriptor, DomainCapability, DomainGateway
from gemini_live_2.panel.contracts import DataAlias, DataBundle
from gemini_live_2.plan_agent import CreatePlanDecision, PlanAgent, PlanAgentError, PlanAgentRequest
from gemini_live_2.settings import Settings
from gemini_live_2.widgets import build_default_widget_registry


class _Response:
    def __init__(self, text: str | None = None, calls: list[types.FunctionCall] | None = None) -> None:
        self.text = text
        self.function_calls = calls or []


class _Models:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def generate_content(self, **kwargs: object) -> _Response:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _Client:
    def __init__(self, responses: list[_Response]) -> None:
        self.models = _Models(responses)
        self.aio = type("Aio", (), {"models": self.models})()


class _CerebrasMessage:
    def __init__(self, content: str | None = None, tool_calls: list[object] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, **_: object) -> dict[str, object]:
        return {"role": "assistant", "content": self.content, "tool_calls": self.tool_calls}


class _CerebrasToolCall:
    def __init__(self, *, call_id: str, name: str, arguments: dict[str, object]) -> None:
        self.id = call_id
        self.function = type("Function", (), {
            "name": name,
            "arguments": json.dumps(arguments),
        })()


class _CerebrasCompletions:
    def __init__(self, responses: list[_CerebrasMessage]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        choice = type("Choice", (), {"message": self.responses.pop(0)})()
        return type("Completion", (), {"choices": [choice]})()


class _CerebrasClient:
    def __init__(self, responses: list[_CerebrasMessage]) -> None:
        self.completions = _CerebrasCompletions(responses)
        self.chat = type("Chat", (), {"completions": self.completions})()


class PlanAgentTests(unittest.TestCase):
    def test_create_plan_requires_describing_its_widgets_first(self) -> None:
        with _domain_root([]) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-widget-1", name="describe_widgets", args={"widget_ids": ["image"]},
                )]),
                _Response(_create_plan_json()),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            result = asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Hiển thị chú chó.")))

            self.assertIsInstance(result.decision, CreatePlanDecision)
            self.assertEqual(result.decision.plan.domain_id, "education")
            self.assertEqual(result.decision.template_description, "Một ảnh lớn đặt cạnh tiêu đề.")
            self.assertEqual(result.decision.plan.blocks[0].props["asset_id"], "dog")
            self.assertEqual(len(client.models.calls), 2)
            config = client.models.calls[0]["config"]
            self.assertEqual(
                [item.name for item in config.tools[0].function_declarations],
                ["describe_widgets", "describe_template"],
            )
            payload = json.loads(client.models.calls[0]["contents"][0].parts[0].text)
            self.assertEqual(
                payload["widget_index"],
                [
                    {"id": "text", "purpose": "Hiển thị văn bản tự do như tiêu đề, nhãn hoặc nội dung ngắn."},
                    {"id": "image", "purpose": "Hiển thị một ảnh hoặc minh hoạ từ Asset Catalog."},
                ],
            )

    def test_native_function_call_returns_function_response_before_final_plan(self) -> None:
        with _domain_root(["lookup_lesson"]) as root:
            registry = DomainRegistry(root)
            gateway = DomainGateway(registry)
            gateway.register(DomainCapability(
                domain_id="education",
                descriptor=CapabilityDescriptor(
                    id="lookup_lesson", description="Lấy nội dung bài học.", input_schema={"type": "object"}
                ),
                handler=lambda arguments: DataBundle(
                    domain_id="education",
                    data={"lesson": {"title": str(arguments["topic"])}},
                    aliases=(DataAlias(id="$lesson_title", path=("lesson", "title"), description="Tiêu đề."),),
                ),
            ))
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-call-1", name="call_capability",
                    args={"capability_id": "lookup_lesson", "arguments": {"topic": "động vật"}},
                )]),
                _Response(calls=[types.FunctionCall(
                    id="native-widget-1", name="describe_widgets", args={"widget_ids": ["text"]},
                )]),
                _Response(json.dumps({
                    "decision": "create_plan",
                    "template_description": "Một tiêu đề phía trên.",
                    "plan": {
                        "blocks": [{
                            "widget_id": "text",
                            "grid": {"col": 1, "row": 1, "col_span": 12, "row_span": 1},
                            "props": {"content": "$lesson_title"},
                        }],
                    },
                }, ensure_ascii=False)),
            ])
            agent = _agent(root, gateway, client)

            result = asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Tạo bài học.")))

            self.assertEqual(result.data_bundle.data["lesson"]["title"], "động vật")
            self.assertEqual(len(client.models.calls), 3)
            config = client.models.calls[0]["config"]
            self.assertEqual(
                [item.name for item in config.tools[0].function_declarations],
                ["describe_widgets", "describe_template", "call_capability"],
            )
            function_responses = [
                part.function_response
                for content in client.models.calls[-1]["contents"]
                for part in content.parts
                if getattr(part, "function_response", None) is not None
            ]
            capability_response = next(response for response in function_responses if response.name == "call_capability")
            self.assertEqual(capability_response.id, "native-call-1")
            self.assertEqual(capability_response.response["capability_id"], "lookup_lesson")
            widget_response = next(response for response in function_responses if response.name == "describe_widgets")
            self.assertEqual(widget_response.id, "native-widget-1")
            self.assertEqual(widget_response.name, "describe_widgets")

    def test_create_plan_rejects_widget_that_was_not_described(self) -> None:
        with _domain_root([]) as root:
            client = _Client([_Response(_create_plan_json())])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            with self.assertRaisesRegex(PlanAgentError, "must call describe_widgets before using: image"):
                asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Hiển thị chú chó.")))

    def test_create_plan_rejects_model_generated_domain_id(self) -> None:
        with _domain_root([]) as root:
            client = _Client([_Response(json.dumps({
                "decision": "create_plan",
                "plan": {"domain_id": "education", "blocks": []},
            }))])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            with self.assertRaisesRegex(PlanAgentError, "invalid final decision"):
                asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Tạo panel.")))

    def test_describe_widgets_returns_only_allowed_widget_contracts(self) -> None:
        with _domain_root([]) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-widget-1", name="describe_widgets",
                    args={"widget_ids": ["text", "image"]},
                )]),
                _Response(_create_plan_json()),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Hiển thị chú chó.")))

            response = client.models.calls[1]["contents"][-1].parts[0].function_response
            self.assertEqual(response.name, "describe_widgets")
            self.assertEqual(response.id, "native-widget-1")
            self.assertEqual(
                response.response["widgets"][1]["props"]["asset_id"]["source"],
                "asset_catalog.id",
            )

    def test_create_plan_accepts_initial_visibility_after_widget_discovery(self) -> None:
        with _domain_root([], allowed_widget_ids=["answer"]) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-widget-1", name="describe_widgets", args={"widget_ids": ["answer"]},
                )]),
                _Response(json.dumps({
                    "decision": "create_plan",
                    "template_description": "Một đáp án ở giữa panel.",
                    "plan": {
                        "blocks": [{
                            "widget_id": "answer",
                            "initial_visibility": "hidden",
                            "grid": {"col": 5, "row": 4, "col_span": 3, "row_span": 2},
                            "props": {"value": "3"},
                        }],
                    },
                })),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            result = asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Tạo đáp án ẩn.")))

            self.assertEqual(result.decision.plan.blocks[0].initial_visibility, "hidden")
            response = client.models.calls[1]["contents"][-1].parts[0].function_response.response
            self.assertEqual(response["widgets"][0]["initial_visibility"]["default"], "visible")
            self.assertEqual(response["widgets"][0]["initial_visibility"]["allowed_values"], ["visible", "hidden"])

    def test_describe_widgets_rejects_widget_outside_domain_scope(self) -> None:
        with _domain_root([]) as root:
            client = _Client([_Response(calls=[types.FunctionCall(
                id="native-widget-1", name="describe_widgets",
                args={"widget_ids": ["object_group"]},
            )])])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            with self.assertRaisesRegex(PlanAgentError, "not allowed by the active domain"):
                asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Tạo nhóm.")))

    def test_gateway_rejects_ungranted_native_capability(self) -> None:
        with _domain_root([]) as root:
            client = _Client([_Response(calls=[types.FunctionCall(
                id="native-call-1", name="call_capability",
                args={"capability_id": "not_granted", "arguments": {}},
            )])])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            with self.assertRaisesRegex(PlanAgentError, "not granted"):
                asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Cần dữ liệu.")))

    def test_final_existing_plan_must_belong_to_catalog(self) -> None:
        with _domain_root([], layout_template=True) as root:
            client = _Client([_Response(json.dumps({
                "decision": "use_existing_plan", "template_id": "missing"
            }))])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            with self.assertRaisesRegex(PlanAgentError, "not in the domain template catalog"):
                asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Dùng plan.")))


class PlanAgentTemplateToolTests(unittest.TestCase):
    def test_describe_template_returns_the_layout_binding_contract(self) -> None:
        with _domain_root([], layout_template=True) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-template-1", name="describe_template", args={"template_id": "present"},
                )]),
                _Response(json.dumps({
                    "decision": "use_existing_plan",
                    "template_id": "present",
                    "bindings": {"$block_1_content": "A title"},
                })),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Use template.")))

            response = client.models.calls[1]["contents"][-1].parts[0].function_response
            self.assertEqual(response.name, "describe_template")
            self.assertEqual(response.id, "native-template-1")
            self.assertEqual(response.response["bindings"][0]["key"], "$block_1_content")

    def test_layout_template_requires_describe_template_and_complete_bindings(self) -> None:
        with _domain_root([], layout_template=True) as root:
            client = _Client([_Response(json.dumps({
                "decision": "use_existing_plan",
                "template_id": "present",
                "bindings": {"$block_1_content": "A title"},
            }))])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            with self.assertRaisesRegex(PlanAgentError, "must call describe_template"):
                asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Use template.")))


class _CerebrasProviderTests(unittest.TestCase):
    def test_uses_openai_compatible_chat_completion(self) -> None:
        with _domain_root([], layout_template=True) as root:
            client = _CerebrasClient([
                _CerebrasMessage(tool_calls=[_CerebrasToolCall(
                    call_id="template-1",
                    name="describe_template",
                    arguments={"template_id": "present"},
                )]),
                _CerebrasMessage(json.dumps({
                    "decision": "use_existing_plan",
                    "template_id": "present",
                    "bindings": {"$block_1_content": "A title"},
                })),
            ])
            settings = replace(
                _settings(),
                planner_provider="cerebras",
                cerebras_api_key="cerebras-test-key",
                cerebras_planner_model="gpt-oss-120b",
            )
            factory_calls: list[dict[str, object]] = []
            agent = PlanAgent(
                settings,
                domain_registry=DomainRegistry(root),
                domain_gateway=DomainGateway(DomainRegistry(root)),
                widget_registry=build_default_widget_registry(),
                cerebras_client_factory=lambda **kwargs: (factory_calls.append(kwargs) or client),
            )

            result = asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Use plan.")))

            self.assertEqual(result.decision.template_id, "present")
            self.assertEqual(factory_calls, [{
                "api_key": "cerebras-test-key",
                "base_url": "https://api.cerebras.ai/v1",
            }])
            self.assertEqual(client.completions.calls[0]["model"], "gpt-oss-120b")
            self.assertEqual(client.completions.calls[0]["tools"][0]["function"]["name"], "describe_widgets")


def _create_plan_json() -> str:
    return json.dumps({
        "decision": "create_plan",
        "template_description": "Một ảnh lớn đặt cạnh tiêu đề.",
        "plan": {
            "blocks": [{
                "widget_id": "image",
                "grid": {"col": 1, "row": 1, "col_span": 6, "row_span": 8},
                "props": {"asset_id": "dog", "label": "Chó"},
            }],
        },
    }, ensure_ascii=False)


def _agent(root: Path, gateway: DomainGateway, client: _Client) -> PlanAgent:
    return PlanAgent(
        _settings(), domain_registry=DomainRegistry(root), domain_gateway=gateway,
        widget_registry=build_default_widget_registry(),
        client_factory=lambda **_kwargs: client,
    )


@contextmanager
def _domain_root(
    capabilities: list[str],
    *,
    layout_template: bool = False,
    allowed_widget_ids: list[str] | None = None,
):
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        domain_root = root / "education"
        assets = domain_root / "assets"
        assets.mkdir(parents=True)
        (assets / "dog.png").write_bytes(b"placeholder")
        manifest: dict[str, object] = {
            "domain_id": "education",
            "asset_catalog_path": "assets/catalog.json",
            "presentation_prompt_path": "prompt.py",
            "presentation_prompt_constant": "PRESENTATION_INSTRUCTION",
            "allowed_widget_ids": allowed_widget_ids or ["text", "image"],
            "tool_capabilities": capabilities,
        }
        if layout_template:
            plans = domain_root / "plans"
            plans.mkdir()
            manifest["template_catalog_path"] = "plans/catalog.json"
            entry: dict[str, str] = {"id": "present", "description": "Plan"}
            entry["layout_path"] = "plans/present.layout.json"
            (plans / "present.layout.json").write_text(json.dumps({
                "template_id": "present",
                "domain_id": "education",
                "description": "Plan",
                "blocks": [{
                    "widget_id": "text",
                    "grid": {"col": 1, "row": 1, "col_span": 12, "row_span": 1},
                    "props": {"content": "$block_1_content", "role": "title"},
                }],
                "bindings": [{
                    "key": "$block_1_content", "block_index": 1, "prop_name": "content",
                    "type": "string", "required": True, "description": "Title",
                }],
            }), encoding="utf-8")
            (plans / "catalog.json").write_text(json.dumps({
                "domain_id": "education", "templates": [entry],
            }), encoding="utf-8")
        (domain_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (domain_root / "prompt.py").write_text('PRESENTATION_INSTRUCTION = "Test prompt"\n', encoding="utf-8")
        (assets / "catalog.json").write_text(json.dumps({
            "domain_id": "education",
            "assets": [{
                "id": "dog", "kind": "image", "path": "assets/dog.png", "mime_type": "image/png",
                "caption": "Chú chó", "tags": ["chó"],
            }],
        }, ensure_ascii=False), encoding="utf-8")
        yield root


def _settings() -> Settings:
    return Settings(
        gemini_live_api_key="live", gemini_live_model="live-model", gemini_live_voice="kore",
        live_turn_timeout_seconds=45, live_idle_timeout_seconds=900, live_reconnect_grace_seconds=30,
        presentation_animation_delay_ms=0, plan_agent_api_key="plan", plan_agent_model="plan-model",
    )
