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
from gemini_live_2.panel.contracts import (
    ActiveSurfaceSummary,
    CreateSurfacePlan,
    DataAlias,
    DataBundle,
    PatchSurfacePlan,
    UseExistingSurfaceTemplate,
)
from gemini_live_2.plan_agent import PlanAgent, PlanAgentError, PlanAgentRequest
from gemini_live_2.settings import Settings
from gemini_live_2.tests.runtime_registry import runtime_widget_registry


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

    def test_base_prompt_requires_activity_first_before_final_surface_plan(self) -> None:
        from gemini_live_2.plan_agent.prompts import CORE_SURFACE_LIFECYCLE_INSTRUCTION

        self.assertIn("QUY TRÌNH SUY NGHĨ NỘI BỘ BẮT BUỘC TRƯỚC KHI TẠO SURFACE", CORE_SURFACE_LIFECYCLE_INSTRUCTION)
        self.assertIn("Final Surface Plan", CORE_SURFACE_LIFECYCLE_INSTRUCTION)
        self.assertIn("CONTRACT SURFACE PLAN", CORE_SURFACE_LIFECYCLE_INSTRUCTION)
        self.assertIn("ROOT_BLOCK", CORE_SURFACE_LIFECYCLE_INSTRUCTION)
        self.assertIn('"op":"replace_children"', CORE_SURFACE_LIFECYCLE_INSTRUCTION)
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

            self.assertIsInstance(result.command, CreateSurfacePlan)
            self.assertEqual(result.command.blocks[0].props["asset_id"], "dog")
            self.assertEqual(len(client.models.calls), 2)
            config = client.models.calls[0]["config"]
            self.assertIn("create_surface_plan", config.system_instruction)
            self.assertIn("Test plan prompt", config.system_instruction)
            self.assertNotIn('"decision":"create_plan"', config.system_instruction)
            self.assertEqual(
                [item.name for item in config.tools[0].function_declarations],
                ["describe_widgets", "describe_template", "search_web", "search_image"],
            )
            payload = json.loads(client.models.calls[0]["contents"][0].parts[0].text)
            self.assertIsNone(payload["active_surface_summary"])
            self.assertEqual(
                {item["id"] for item in payload["widget_index"]},
                set(runtime_widget_registry().widget_ids()),
            )

    def test_runtime_feedback_is_sent_as_structured_plan_agent_context(self) -> None:
        with _domain_root([]) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-widget-1", name="describe_widgets", args={"widget_ids": ["image"]},
                )]),
                _Response(_create_plan_json()),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)
            feedback = {
                "kind": "runtime_feedback",
                "surface_id": "panel-a",
                "revision": 3,
                "component_id": "2",
                "anchor_id": "b",
                "error_type": "image_load_failed",
                "observed": {"status": "error", "retry_attempts": 1},
                "repair_scope": "surface_plan",
            }

            asyncio.run(agent.plan(PlanAgentRequest(
                domain_id="education", intent="Sửa ảnh bị lỗi.", runtime_feedback=feedback,
            )))

            payload = json.loads(client.models.calls[0]["contents"][0].parts[0].text)
            self.assertEqual(payload["runtime_feedback"], feedback)

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
                    "action": "create_surface_plan",
                    "template_description": "Một tiêu đề bài học.",
                    "surface": {
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
                ["describe_widgets", "describe_template", "search_web", "search_image", "call_capability"],
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

    def test_retrieval_results_accumulate_across_multiple_capability_calls(self) -> None:
        with _domain_root(["search_web", "search_image"]) as root:
            gateway = DomainGateway(DomainRegistry(root))
            gateway.register(DomainCapability(
                domain_id="education",
                descriptor=CapabilityDescriptor("search_web", "Search web.", {"type": "object"}),
                handler=lambda _: DataBundle(domain_id="education", data={"search_results": [{
                    "kind": "web", "result_id": "web_1", "title": "Vòng đời bướm",
                    "snippet": "Trứng, sâu, nhộng, bướm.", "source_url": "https://example/web",
                }]}),
            ))
            gateway.register(DomainCapability(
                domain_id="education",
                descriptor=CapabilityDescriptor("search_image", "Search image.", {"type": "object"}),
                handler=lambda _: DataBundle(domain_id="education", data={"search_results": [{
                    "kind": "image", "result_id": "img_1", "caption": "Vòng đời bướm",
                    "source_url": "https://example/image",
                }]}),
            ))
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="web-1", name="call_capability",
                    args={"capability_id": "search_web", "arguments": {"query": "vòng đời bướm"}},
                )]),
                _Response(calls=[types.FunctionCall(
                    id="image-1", name="call_capability",
                    args={"capability_id": "search_image", "arguments": {"query": "ảnh vòng đời bướm"}},
                )]),
                _Response(calls=[types.FunctionCall(
                    id="widget-1", name="describe_widgets", args={"widget_ids": ["text"]},
                )]),
                _Response(json.dumps({
                    "action": "create_surface_plan",
                    "template_description": "Một tiêu đề bài học.",
                    "surface": {"blocks": [{
                        "widget_id": "text",
                        "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1},
                        "props": {"content": "Cùng quan sát vòng đời bướm nhé!"},
                    }]},
                }, ensure_ascii=False)),
            ])

            result = asyncio.run(_agent(root, gateway, client).plan(PlanAgentRequest(
                domain_id="education", intent="Dạy vòng đời bướm.", session_id="session-a",
            )))

            results = result.data_bundle.data["search_results"]
            self.assertEqual([item["kind"] for item in results], ["web", "image"])
            self.assertEqual([item["result_id"] for item in results], ["web_1", "img_1"])
            self.assertEqual(json.loads(client.models.calls[0]["contents"][0].parts[0].text)["tool_budget"], {
                "max_native_calls": 10,
            })

    def test_tool_limit_returns_feedback_once_then_allows_a_final_plan(self) -> None:
        with _domain_root(["lookup_lesson"]) as root:
            gateway = DomainGateway(DomainRegistry(root))
            gateway.register(DomainCapability(
                domain_id="education",
                descriptor=CapabilityDescriptor("lookup_lesson", "Lookup.", {"type": "object"}),
                handler=lambda _: DataBundle(domain_id="education", data={"lesson": {"title": "Bướm"}}),
            ))
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="widget-1", name="describe_widgets", args={"widget_ids": ["text"]},
                )]),
                _Response(calls=[types.FunctionCall(
                    id="capability-1", name="call_capability",
                    args={"capability_id": "lookup_lesson", "arguments": {}},
                )]),
                _Response(json.dumps({
                    "action": "create_surface_plan",
                    "template_description": "Một tiêu đề.",
                    "surface": {"blocks": [{
                        "widget_id": "text",
                        "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1},
                        "props": {"content": "Bài học về bướm"},
                    }]},
                }, ensure_ascii=False)),
            ])

            result = asyncio.run(_agent(root, gateway, client, max_tool_steps=1).plan(
                PlanAgentRequest(domain_id="education", intent="Tạo bài học.")
            ))

            self.assertIsInstance(result.command, CreateSurfacePlan)
            limit_responses = [
                part.function_response.response
                for content in client.models.calls[-1]["contents"]
                for part in content.parts
                if getattr(part, "function_response", None) is not None
            ]
            self.assertEqual(limit_responses[-1]["error"]["code"], "tool_limit_reached")

    def test_tool_call_after_limit_feedback_is_rejected(self) -> None:
        with _domain_root([]) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="widget-1", name="describe_widgets", args={"widget_ids": ["text"]},
                )]),
                _Response(calls=[types.FunctionCall(
                    id="widget-2", name="describe_widgets", args={"widget_ids": ["image"]},
                )]),
                _Response(calls=[types.FunctionCall(
                    id="widget-3", name="describe_widgets", args={"widget_ids": ["text"]},
                )]),
            ])
            with self.assertRaisesRegex(PlanAgentError, "after receiving tool_limit_reached"):
                asyncio.run(_agent(
                    root, DomainGateway(DomainRegistry(root)), client, max_tool_steps=1,
                ).plan(PlanAgentRequest(domain_id="education", intent="Tạo bài học.")))

    def test_tool_limit_is_enforced_inside_one_multi_call_response(self) -> None:
        with _domain_root([]) as root:
            client = _Client([
                _Response(calls=[
                    types.FunctionCall(id="widget-1", name="describe_widgets", args={"widget_ids": ["text"]}),
                    types.FunctionCall(id="widget-2", name="describe_widgets", args={"widget_ids": ["image"]}),
                ]),
                _Response(json.dumps({
                    "action": "create_surface_plan",
                    "template_description": "Một tiêu đề.",
                    "surface": {"blocks": [{
                        "widget_id": "text",
                        "grid": {"col": 1, "row": 1, "col_span": 16, "row_span": 1},
                        "props": {"content": "Bài học về bướm"},
                    }]},
                }, ensure_ascii=False)),
            ])
            result = asyncio.run(_agent(
                root, DomainGateway(DomainRegistry(root)), client, max_tool_steps=1,
            ).plan(PlanAgentRequest(domain_id="education", intent="Tạo bài học.")))

            self.assertIsInstance(result.command, CreateSurfacePlan)
            function_responses = [
                part.function_response.response
                for content in client.models.calls[-1]["contents"]
                for part in content.parts
                if getattr(part, "function_response", None) is not None
            ]
            self.assertEqual(function_responses[-1]["error"]["code"], "tool_limit_reached")

    def test_create_plan_allows_widget_without_describing_it_first(self) -> None:
        with _domain_root([]) as root:
            client = _Client([_Response(_create_plan_json())])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            result = asyncio.run(agent.plan(PlanAgentRequest(
                domain_id="education", intent="Hiển thị chú chó."
            )))

            self.assertIsInstance(result.command, CreateSurfacePlan)
            self.assertEqual(result.command.blocks[0].widget_id, "image")

    def test_create_plan_rejects_model_generated_domain_id(self) -> None:
        with _domain_root([]) as root:
            client = _Client([_Response(json.dumps({
                "action": "create_surface_plan",
                "template_description": "Không hợp lệ.",
                "surface": {"domain_id": "education", "blocks": []},
            }))])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            with self.assertRaisesRegex(PlanAgentError, "invalid final decision"):
                asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Tạo panel.")))

    def test_patch_surface_plan_receives_and_uses_active_surface_summary(self) -> None:
        with _domain_root([]) as root:
            client = _Client([_Response(json.dumps({
                "action": "patch_surface_plan",
                "surface_id": "s12",
                "base_revision": 3,
                "operations": [{
                    "op": "update_props",
                    "anchor_id": "b",
                    "changes": {"asset_id": "cat", "label": "Mèo"},
                }],
            }))])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)
            summary = ActiveSurfaceSummary(
                surface_id="s12",
                revision=3,
                domain_id="education",
                purpose="Hiển thị chó.",
                structure_summary=({"anchor_id": "b", "widget": "image", "description": "Chó"},),
                state_summary={},
            )

            result = asyncio.run(agent.plan(PlanAgentRequest(
                domain_id="education", intent="Đổi sang mèo.", active_surface_summary=summary,
            )))

            self.assertIsInstance(result.command, PatchSurfacePlan)
            self.assertEqual(result.command.surface_id, "s12")
            payload = json.loads(client.models.calls[0]["contents"][0].parts[0].text)
            self.assertEqual(payload["active_surface_summary"]["revision"], 3)
            self.assertEqual(payload["active_surface_summary"]["structure_summary"][0]["anchor_id"], "b")

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

    def test_create_plan_accepts_initial_state_after_widget_discovery(self) -> None:
        with _domain_root([]) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-widget-1", name="describe_widgets", args={"widget_ids": ["answer"]},
                )]),
                _Response(json.dumps({
                    "action": "create_surface_plan",
                    "template_description": "Một đáp án ẩn.",
                    "surface": {
                        "blocks": [{
                            "widget_id": "answer",
                            "initial_state": {"visibility": "hidden"},
                            "grid": {"col": 5, "row": 4, "col_span": 3, "row_span": 2},
                            "props": {"value": "3"},
                        }],
                    },
                })),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            result = asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Tạo đáp án ẩn.")))

            self.assertEqual(result.command.blocks[0].initial_visibility, "hidden")
            self.assertEqual(result.command.blocks[0].initial_state, {"visibility": "hidden"})
            response = client.models.calls[1]["contents"][-1].parts[0].function_response.response
            self.assertEqual(response["widgets"][0]["initial_state"]["default"], {"visibility": "visible"})
            self.assertEqual(
                response["widgets"][0]["initial_state"]["fields"]["visibility"]["allowed_values"],
                ["visible", "hidden"],
            )

    def test_describe_flashcard_returns_state_and_flip_contract(self) -> None:
        with _domain_root([]) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-widget-1", name="describe_widgets", args={"widget_ids": ["flashcard"]},
                )]),
                _Response(json.dumps({
                    "action": "create_surface_plan",
                    "template_description": "Một thẻ từ vựng lớn ở giữa.",
                    "surface": {"blocks": [{
                        "widget_id": "flashcard",
                        "grid": {"col": 4, "row": 2, "col_span": 9, "row_span": 7},
                        "props": {
                            "front": {"asset_id": "dog", "text": "Chó"},
                            "back": {"word": "DOG", "phonetic": "/dɒɡ/", "meaning": "con chó"},
                        },
                        "initial_state": {"flipped": False},
                    }]},
                }, ensure_ascii=False)),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            result = asyncio.run(agent.plan(PlanAgentRequest(
                domain_id="education", intent="Học từ DOG bằng thẻ lật."
            )))

            self.assertEqual(result.command.blocks[0].initial_state, {"flipped": False})
            response = client.models.calls[1]["contents"][-1].parts[0].function_response.response["widgets"][0]
            self.assertEqual(response["initial_state"]["default"], {"visibility": "visible", "flipped": False})
            self.assertEqual(response["interactions"][0]["action"], "flip")
            self.assertEqual(response["interactions"][0]["state_rule"], {"flipped": {"op": "toggle"}})

    def test_create_plan_with_choice_requires_describing_the_choice_and_its_children(self) -> None:
        with _domain_root([]) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-widget-1",
                    name="describe_widgets",
                    args={"widget_ids": ["choice", "image", "text"]},
                )]),
                _Response(json.dumps({
                    "action": "create_surface_plan",
                    "template_description": "Một thẻ lựa chọn.",
                    "surface": {"blocks": [{
                        "widget_id": "choice",
                        "grid": {"col": 1, "row": 2, "col_span": 4, "row_span": 5},
                        "props": {},
                        "children": [
                            {"widget_id": "image", "props": {"asset_id": "dog"}},
                            {"widget_id": "text", "props": {"content": "Chó", "role": "label"}},
                        ],
                    }]},
                }, ensure_ascii=False)),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            result = asyncio.run(agent.plan(PlanAgentRequest(
                domain_id="education", intent="Chọn đúng con chó."
            )))

            choice = result.command.blocks[0]
            self.assertEqual(choice.widget_id, "choice")
            self.assertEqual(choice.props, {})
            self.assertEqual([child.widget_id for child in choice.children], ["image", "text"])

    def test_create_plan_allows_choice_child_without_describing_it_first(self) -> None:
        with _domain_root([]) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-widget-1",
                    name="describe_widgets",
                    args={"widget_ids": ["choice"]},
                )]),
                _Response(json.dumps({
                    "action": "create_surface_plan",
                    "template_description": "Một thẻ lựa chọn.",
                    "surface": {"blocks": [{
                        "widget_id": "choice",
                        "grid": {"col": 1, "row": 2, "col_span": 4, "row_span": 5},
                        "props": {},
                        "children": [{"widget_id": "image", "props": {"asset_id": "dog"}}],
                    }]},
                }, ensure_ascii=False)),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            result = asyncio.run(agent.plan(PlanAgentRequest(
                domain_id="education", intent="Chọn đúng con chó."
            )))

            self.assertIsInstance(result.command, CreateSurfacePlan)
            self.assertEqual(result.command.blocks[0].children[0].widget_id, "image")

    def test_describe_widgets_allows_any_installed_widget(self) -> None:
        with _domain_root([]) as root:
            client = _Client([_Response(calls=[types.FunctionCall(
                id="native-widget-1", name="describe_widgets",
                args={"widget_ids": ["object_group"]},
            )]), _Response(json.dumps({
                "action": "create_surface_plan",
                "template_description": "x",
                "surface": {"blocks": [{
                    "widget_id": "object_group",
                    "grid": {"col": 1, "row": 1, "col_span": 4, "row_span": 4},
                    "props": {"asset_id": "dog", "count": 1},
                }]},
            }))])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            result = asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Tạo nhóm.")))
            self.assertIsInstance(result.command, CreateSurfacePlan)
            self.assertEqual(result.command.blocks[0].widget_id, "object_group")

    def test_gateway_rejects_ungranted_native_capability(self) -> None:
        with _domain_root([]) as root:
            client = _Client([_Response(calls=[types.FunctionCall(
                id="native-call-1", name="call_capability",
                args={"capability_id": "not_granted", "arguments": {}},
            )])])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            with self.assertRaisesRegex(PlanAgentError, "not granted"):
                asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Cần dữ liệu.")))

    def test_legacy_template_decision_is_rejected(self) -> None:
        with _domain_root([], layout_template=True) as root:
            client = _Client([_Response(json.dumps({
                "decision": "use_existing_plan", "template_id": "missing"
            }))])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            with self.assertRaisesRegex(PlanAgentError, "invalid final decision"):
                asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Dùng plan.")))

    def test_existing_template_requires_describe_then_returns_only_bindings(self) -> None:
        with _domain_root([], layout_template=True) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-template-1", name="describe_template", args={"template_id": "present"},
                )]),
                _Response(json.dumps({
                    "action": "use_existing_surface_template",
                    "template_id": "present",
                    "bindings": {"$block_1_content": "Cùng học nhé!"},
                }, ensure_ascii=False)),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            result = asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Dùng khung.")))

            self.assertIsInstance(result.command, UseExistingSurfaceTemplate)
            self.assertEqual(result.command.template_id, "present")
            self.assertEqual(result.command.bindings, {"$block_1_content": "Cùng học nhé!"})


class PlanAgentTemplateToolTests(unittest.TestCase):
    def test_describe_template_returns_the_layout_structure_for_create_surface(self) -> None:
        with _domain_root([], layout_template=True) as root:
            client = _Client([
                _Response(calls=[types.FunctionCall(
                    id="native-template-1", name="describe_template", args={"template_id": "present"},
                )]),
                _Response(calls=[types.FunctionCall(
                    id="native-widget-1", name="describe_widgets", args={"widget_ids": ["text"]},
                )]),
                _Response(json.dumps({
                    "action": "create_surface_plan",
                    "template_description": "Một tiêu đề bài học.",
                    "surface": {"blocks": [{
                        "widget_id": "text",
                        "grid": {"col": 1, "row": 1, "col_span": 12, "row_span": 1},
                        "props": {"content": "A title", "role": "title"},
                    }]},
                })),
            ])
            agent = _agent(root, DomainGateway(DomainRegistry(root)), client)

            asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Use template.")))

            responses = [
                part.function_response
                for call in client.models.calls
                for content in call["contents"]
                for part in content.parts
                if getattr(part, "function_response", None) is not None
            ]
            response = next(item for item in responses if item.name == "describe_template")
            self.assertEqual(response.id, "native-template-1")
            self.assertEqual(response.response["bindings"][0]["key"], "$block_1_content")
            self.assertEqual(response.response["blocks"][0]["widget_id"], "text")
            self.assertEqual(response.response["semantic_spec"]["component_contracts"][0]["widget_id"], "text")


class _CerebrasProviderTests(unittest.TestCase):
    def test_uses_openai_compatible_chat_completion(self) -> None:
        with _domain_root([], layout_template=True) as root:
            client = _CerebrasClient([
                _CerebrasMessage(tool_calls=[_CerebrasToolCall(
                    call_id="widget-1", name="describe_widgets", arguments={"widget_ids": ["text"]},
                )]),
                _CerebrasMessage(json.dumps({
                    "action": "create_surface_plan",
                    "template_description": "Một tiêu đề bài học.",
                    "surface": {"blocks": [{
                        "widget_id": "text",
                        "grid": {"col": 1, "row": 1, "col_span": 12, "row_span": 1},
                        "props": {"content": "A title", "role": "title"},
                    }]},
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
                widget_registry=runtime_widget_registry(),
                cerebras_client_factory=lambda **kwargs: (factory_calls.append(kwargs) or client),
            )

            result = asyncio.run(agent.plan(PlanAgentRequest(domain_id="education", intent="Use plan.")))

            self.assertIsInstance(result.command, CreateSurfacePlan)
            self.assertEqual(factory_calls, [{
                "api_key": "cerebras-test-key",
                "base_url": "https://api.cerebras.ai/v1",
            }])
            self.assertEqual(client.completions.calls[0]["model"], "gpt-oss-120b")
            self.assertEqual(client.completions.calls[0]["tools"][0]["function"]["name"], "describe_widgets")
            self.assertIn("Test plan prompt", client.completions.calls[0]["messages"][0]["content"])


def _create_plan_json() -> str:
    return json.dumps({
        "action": "create_surface_plan",
        "template_description": "Một ảnh minh hoạ.",
        "surface": {
            "blocks": [{
                "widget_id": "image",
                "grid": {"col": 1, "row": 1, "col_span": 6, "row_span": 8},
                "props": {"asset_id": "dog", "label": "Chó"},
            }],
        },
    }, ensure_ascii=False)


def _agent(
    root: Path,
    gateway: DomainGateway,
    client: _Client,
    *,
    max_tool_steps: int = 10,
) -> PlanAgent:
    return PlanAgent(
        _settings(), domain_registry=DomainRegistry(root), domain_gateway=gateway,
        widget_registry=runtime_widget_registry(),
        client_factory=lambda **_kwargs: client,
        max_tool_steps=max_tool_steps,
    )


@contextmanager
def _domain_root(
    capabilities: list[str],
    *,
    layout_template: bool = False,
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
            "plan_prompt_path": "plan_prompt.py",
            "plan_prompt_constant": "PLAN_INSTRUCTION",
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
        (domain_root / "plan_prompt.py").write_text('PLAN_INSTRUCTION = "Test plan prompt"\n', encoding="utf-8")
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
