"""CP10 routing tests: Live selects only the top-level route_request tool."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from gemini_live_2.catalogs.domains import DomainRegistry
from gemini_live_2.gateway import DomainGateway
from gemini_live_2.live.orchestrator import LiveSessionOrchestrator
from gemini_live_2.live.gemini_session import GeminiLiveSession
from gemini_live_2.live.registry import LiveToolRegistry
from gemini_live_2.panel import (
    ActivePanelState,
    AnchorBinding,
    ComponentChild,
    ChoiceChild,
    ComponentNode,
    CreateSurfacePlan,
    DataBundle,
    GridRect,
    MoveBlockOperation,
    PanelCompilationError,
    PanelCompiler,
    PlanBlock,
    PatchSurfacePlan,
    SurfaceDocument,
    UpdatePropsOperation,
    ReplaceChildrenOperation,
    UseExistingSurfaceTemplate,
    render_visual_stage_map,
)
from gemini_live_2.plan_agent import PlanAgentResult
from gemini_live_2.settings import Settings
from gemini_live_2.widgets import build_default_widget_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _plan_block(
    widget_id: str,
    col: int,
    row: int,
    col_span: int,
    row_span: int,
    props: dict[str, object],
) -> PlanBlock:
    return PlanBlock(
        widget_id=widget_id,
        grid=GridRect(col, row, col_span, row_span),
        props=props,
    )


class _PlanAgentStub:
    def __init__(self) -> None:
        self.requests = []
        self.command = CreateSurfacePlan(
            blocks=(
                _plan_block("text", 1, 1, 16, 1, {"content": "Cùng quan sát chó và mèo nhé!", "role": "title"}),
                _plan_block("image", 1, 3, 6, 5, {"asset_id": "dog", "label": "Chó"}),
                _plan_block("image", 10, 3, 6, 5, {"asset_id": "cat", "label": "Mèo"}),
            ),
        )

    async def plan(self, request):
        self.requests.append(request)
        return PlanAgentResult(
            command=self.command,
            data_bundle=DataBundle(domain_id=request.domain_id, data={}),
        )


class _RepairingCompiler:
    """Reject once, then delegate to the real compiler."""

    def __init__(self) -> None:
        self.widget_registry = build_default_widget_registry()
        self._compiler = PanelCompiler(self.widget_registry)
        self.calls = 0

    def compile_surface_document(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise PanelCompilationError(
                "blocks 4 and 6 overlap.",
                code="grid_overlap",
                details={
                    "first_block_index": 4,
                    "second_block_index": 6,
                    "overlap_cells": [{"col": 7, "row": 6}],
                },
            )
        return self._compiler.compile_surface_document(**kwargs)


class LiveRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = DomainRegistry(PROJECT_ROOT / "domains")
        self.agent = _PlanAgentStub()
        self.orchestrator = LiveSessionOrchestrator(
            domain_registry=self.registry,
            plan_agent=self.agent,  # type: ignore[arg-type]
            panel_compiler=PanelCompiler(build_default_widget_registry()),
        )

    def test_registry_exposes_only_route_request_with_registered_domain_enum(self) -> None:
        declaration = LiveToolRegistry(("education",)).tool_declarations()
        self.assertEqual([item["name"] for item in declaration], ["route_request"])
        self.assertEqual(declaration[0]["parameters"]["properties"]["domain_id"]["enum"], ["education"])

    def test_route_compiles_renders_and_returns_panel_context(self) -> None:
        self.orchestrator.remember_turn(session_id="s1", user_text="Cho bé xem hai con vật", assistant_text="")
        result = asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Cho trẻ so sánh chó và mèo."},
        ))
        self.assertEqual(result.response["status"], "completed")
        self.assertIn("VISUAL STAGE MAP", result.response["visual_stage_map"])
        self.assertIsNotNone(result.presentation)
        self.assertEqual(result.presentation.panel["ui_type"], "surface_document")
        self.assertEqual(result.presentation.panel["surface"]["revision"], 1)
        self.assertIn("cô giáo thân thiện", result.response["presentation_instruction"])
        self.assertEqual(self.orchestrator.active_panel("s1").revision, 1)
        self.assertEqual(self.agent.requests[0].recent_history[0]["text"], "Cho bé xem hai con vật")

    def test_route_materializes_an_existing_template_from_bindings(self) -> None:
        self.agent.command = UseExistingSurfaceTemplate(
            template_id="two_subject_comparison",
            bindings={
                "$block_1_content": "Cùng quan sát hai bạn mèo nhé!",
                "$block_2_asset_id": "cat",
                "$block_3_asset_id": "cat",
            },
        )
        result = asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Hiển thị hai con mèo."},
        ))

        self.assertEqual(result.response["status"], "completed")
        document = self.orchestrator.active_panel("s1").document
        self.assertEqual([component.props.get("asset_id") for component in document.components[1:]], ["cat", "cat"])

    def test_active_surface_summary_is_business_context_and_is_supplied_only_on_later_route(self) -> None:
        self.assertIsNone(self.orchestrator.active_surface_summary("s1"))

        asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Show a dog."},
        ))
        summary = self.orchestrator.active_surface_summary("s1")
        self.assertIsNotNone(summary)
        assert summary is not None
        payload = summary.to_dict()
        self.assertEqual(payload["purpose"], "Show a dog.")
        self.assertEqual(payload["domain_id"], "education")
        self.assertEqual(payload["revision"], 1)
        self.assertTrue(payload["structure_summary"])
        self.assertNotIn("grid", payload["structure_summary"][0])
        self.assertNotIn("target_id", payload["structure_summary"][0])

        asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Change to two cats."},
        ))
        passed_to_second_route = self.agent.requests[1].active_surface_summary
        self.assertIsNotNone(passed_to_second_route)
        assert passed_to_second_route is not None
        self.assertEqual(passed_to_second_route.purpose, "Show a dog.")

        self.orchestrator._active_panels.pop("s1")  # type: ignore[attr-defined]
        self.assertIsNone(self.orchestrator.active_surface_summary("s1"))

    def test_active_panel_context_restores_domain_prompt_and_stage_map(self) -> None:
        asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Compare dog and cat."},
        ))
        context = self.orchestrator.active_panel_presentation_context("s1")
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn("cô giáo thân thiện", context["presentation_instruction"])
        self.assertIn("VISUAL STAGE MAP", context["visual_stage_map"])
        self.assertTrue(context["visual_effects"])
        self.assertEqual(context["surface_id"], self.orchestrator.active_panel("s1").document.surface_id)
        self.assertEqual(context["revision"], 1)

    def test_reconnect_instruction_contains_history_and_active_panel_context(self) -> None:
        self.orchestrator.remember_turn(
            session_id="s1", user_text="Cho bé xem chó và mèo.", assistant_text="Được chứ."
        )
        asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Compare dog and cat."},
        ))
        session = GeminiLiveSession(
            settings=Settings(
                gemini_live_api_key="", gemini_live_model="test", gemini_live_voice="kore",
                live_turn_timeout_seconds=45, live_idle_timeout_seconds=900,
                live_reconnect_grace_seconds=30, presentation_animation_delay_ms=0,
                plan_agent_api_key="", plan_agent_model="test",
            ),
            registry=LiveToolRegistry(("education",)),
            orchestrator=self.orchestrator,
        )
        instruction = session._instruction("s1")
        self.assertIn("Cho bé xem chó và mèo.", instruction)
        self.assertIn("cô giáo thân thiện", instruction)
        self.assertIn("PANEL HIỆN TẠI", instruction)
        self.assertIn("VISUAL STAGE MAP", instruction)
        self.assertIn("surface_id:", instruction)
        self.assertIn("base_revision: 1", instruction)

    def test_delete_surface_requires_current_revision_then_removes_panel(self) -> None:
        asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Hiển thị một chú chó."},
        ))
        active = self.orchestrator.active_panel("s1")
        assert active is not None

        with self.assertRaisesRegex(ValueError, "base_revision"):
            self.orchestrator.delete_surface(
                session_id="s1",
                surface_id=active.document.surface_id,
                base_revision=active.revision + 1,
            )
        self.assertIsNotNone(self.orchestrator.active_panel("s1"))

        result = self.orchestrator.delete_surface(
            session_id="s1",
            surface_id=active.document.surface_id,
            base_revision=active.revision,
        )
        self.assertEqual(result.response["status"], "completed")
        self.assertEqual(result.response["revision"], active.revision + 1)
        self.assertEqual(result.response["visual_effects"], [])
        self.assertIn("KHÔNG CÓ PANEL", result.response["visual_stage_map"])
        self.assertIsNone(self.orchestrator.active_panel("s1"))

    def test_present_visual_resolves_only_the_current_panel_anchor_map(self) -> None:
        asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Compare dog and cat."},
        ))
        cue = self.orchestrator.present_visual(
            session_id="s1", anchor_id="b", effect_id="highlight",
        )
        self.assertEqual(cue["panel_revision"], 1)
        self.assertEqual(cue["anchor_id"], "b")
        with self.assertRaisesRegex(ValueError, "unknown anchor_id"):
            self.orchestrator.present_visual(session_id="s1", anchor_id="missing", effect_id="highlight")

    def test_update_surface_state_reveals_hidden_blocks_in_place_and_rejects_repeat(self) -> None:
        document = SurfaceDocument(
            surface_id="panel-hidden",
            domain_id="education",
            revision=1,
            components=(
                ComponentNode(
                    id="1",
                    type="image",
                    layout=GridRect(col=1, row=1, col_span=4, row_span=4),
                    props={"asset_id": "cat", "label": "MÃ¨o"},
                    state={"visibility": "hidden"},
                ),
            ),
            anchors=(
                AnchorBinding(
                    anchor_id="a",
                    component_id="1",
                    anchor_key="image",
                    allowed_effect_ids=("highlight", "circle"),
                ),
            ),
        )
        self.orchestrator._active_panels["s1"] = ActivePanelState(document=document, purpose="Bài test")  # type: ignore[attr-defined]

        result = self.orchestrator.update_surface_state(
            session_id="s1", surface_id="panel-hidden", base_revision=1,
            updates=[{"anchor_id": "a", "changes": {"visibility": "visible"}}],
        )

        updated = self.orchestrator.active_panel("s1")
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.document.surface_id, "panel-hidden")
        self.assertEqual(updated.document.components[0].state["visibility"], "visible")
        self.assertEqual(result.response["revision"], 2)
        self.assertEqual(result.panel_update["surface"]["revision"], 2)
        self.assertEqual(result.panel_update["surface"]["components"][0]["props"]["asset_id"], "cat")
        self.assertEqual(
            self.orchestrator.present_visual(session_id="s1", anchor_id="a", effect_id="highlight")["anchor_id"],
            "a",
        )
        with self.assertRaisesRegex(ValueError, "cannot transition"):
            self.orchestrator.update_surface_state(
                session_id="s1", surface_id="panel-hidden", base_revision=2,
                updates=[{"anchor_id": "a", "changes": {"visibility": "visible"}}],
            )

    def test_panel_interaction_resolves_registered_action_on_current_surface_revision(self) -> None:
        document = SurfaceDocument(
            surface_id="panel-choice",
            domain_id="education",
            revision=1,
            components=(
                ComponentNode(
                    id="1",
                    type="choice",
                    layout=GridRect(col=1, row=1, col_span=4, row_span=4),
                    props={},
                    children=(
                        ComponentChild(type="image", props={"asset_id": "cat"}),
                        ComponentChild(type="text", props={"content": "Mèo", "role": "label"}),
                    ),
                ),
            ),
            anchors=(AnchorBinding("b", "1", "choice", ("highlight", "circle")),),
        )
        self.orchestrator._active_panels["s1"] = ActivePanelState(document=document, purpose="Bài test")  # type: ignore[attr-defined]

        event = self.orchestrator.resolve_panel_interaction(
            session_id="s1", surface_id="panel-choice", revision=1, anchor_id="b", action="select",
        )

        self.assertEqual(event, {
            "event": "surface_interaction",
            "surface_id": "panel-choice",
            "revision": 1,
            "anchor_id": "b",
            "widget_id": "choice",
            "action": "select",
            "content": [
                {"type": "image", "props": {"asset_id": "cat"}},
                {"type": "text", "props": {"content": "Mèo", "role": "label"}},
            ],
        })
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.orchestrator.resolve_panel_interaction(
                session_id="s1", surface_id="stale", revision=1, anchor_id="b", action="select",
            )
        with self.assertRaisesRegex(ValueError, "revision does not match"):
            self.orchestrator.resolve_panel_interaction(
                session_id="s1", surface_id="panel-choice", revision=2, anchor_id="b", action="select",
            )
        with self.assertRaisesRegex(ValueError, "not allowed"):
            self.orchestrator.resolve_panel_interaction(
                session_id="s1", surface_id="panel-choice", revision=1, anchor_id="b", action="reveal",
            )

    def test_flashcard_interaction_commits_flip_snapshot_and_stage_map(self) -> None:
        document = SurfaceDocument(
            surface_id="panel-flashcard",
            domain_id="education",
            revision=1,
            components=(
                ComponentNode(
                    id="1", type="flashcard", layout=GridRect(3, 2, 8, 6),
                    props={
                        "front": {"asset_id": "cat", "text": "Con mèo"},
                        "back": {"word": "CAT", "phonetic": "/kæt/", "meaning": "con mèo"},
                    },
                    state={"visibility": "visible", "flipped": False},
                ),
            ),
            anchors=(AnchorBinding("b", "1", "card", ("highlight", "circle")),),
        )
        self.orchestrator._active_panels["s1"] = ActivePanelState(document=document, purpose="Bài test")  # type: ignore[attr-defined]

        result = self.orchestrator.apply_panel_interaction(
            session_id="s1", surface_id="panel-flashcard", revision=1, anchor_id="b", action="flip",
        )

        active = self.orchestrator.active_panel("s1")
        self.assertIsNotNone(active)
        assert active is not None
        self.assertTrue(active.document.components[0].state["flipped"])
        self.assertEqual(active.document.revision, 2)
        self.assertIsNotNone(result.panel_update)
        self.assertEqual(result.panel_update["surface"]["revision"], 2)  # type: ignore[index]
        self.assertEqual(result.interaction["revision"], 2)
        self.assertIn("TỪ: “CAT”", result.interaction["visual_stage_map"])
        self.assertNotIn("Minh họa một chú mèo", result.interaction["visual_stage_map"])
        with self.assertRaisesRegex(ValueError, "revision does not match"):
            self.orchestrator.apply_panel_interaction(
                session_id="s1", surface_id="panel-flashcard", revision=1, anchor_id="b", action="flip",
            )

    def test_update_surface_state_rejects_unknown_or_duplicate_components_without_changing_state(self) -> None:
        document = SurfaceDocument(
            surface_id="panel-hidden",
            domain_id="education",
            revision=1,
            components=(
                ComponentNode(
                    id="1", type="image", layout=GridRect(1, 1, 4, 4),
                    props={"asset_id": "cat"}, state={"visibility": "hidden"},
                ),
            ),
            anchors=(AnchorBinding("a", "1", "image", ("highlight",)),),
        )
        self.orchestrator._active_panels["s1"] = ActivePanelState(document=document, purpose="Bài test")  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ValueError, "unknown anchor_id"):
            self.orchestrator.update_surface_state(
                session_id="s1", surface_id="panel-hidden", base_revision=1,
                updates=[{"anchor_id": "missing", "changes": {"visibility": "visible"}}],
            )
        with self.assertRaisesRegex(ValueError, "same component"):
            self.orchestrator.update_surface_state(
                session_id="s1", surface_id="panel-hidden", base_revision=1,
                updates=[
                    {"anchor_id": "a", "changes": {"visibility": "visible"}},
                    {"anchor_id": "a", "changes": {"visibility": "visible"}},
                ],
            )
        self.assertEqual(self.orchestrator.active_panel("s1").revision, 1)  # type: ignore[union-attr]

    def test_update_surface_state_reveals_multiple_hidden_components_together(self) -> None:
        document = SurfaceDocument(
            surface_id="panel-hidden",
            domain_id="education",
            revision=1,
            components=(
                ComponentNode("1", "image", GridRect(1, 1, 4, 4), {"asset_id": "cat"}, {"visibility": "hidden"}),
                ComponentNode("2", "answer", GridRect(6, 1, 2, 2), {"value": "3"}, {"visibility": "hidden"}),
            ),
            anchors=(
                AnchorBinding("a", "1", "image", ("highlight",)),
                AnchorBinding("b", "2", "answer", ("circle",)),
            ),
        )
        self.orchestrator._active_panels["s1"] = ActivePanelState(document=document, purpose="Bài test")  # type: ignore[attr-defined]

        result = self.orchestrator.update_surface_state(
            session_id="s1", surface_id="panel-hidden", base_revision=1,
            updates=[
                {"anchor_id": "a", "changes": {"visibility": "visible"}},
                {"anchor_id": "b", "changes": {"visibility": "visible"}},
            ],
        )

        updated = self.orchestrator.active_panel("s1")
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(
            [component.state["visibility"] for component in updated.document.components],
            ["visible", "visible"],
        )
        self.assertEqual(result.response["updated_anchor_ids"], ["a", "b"])

    def test_update_surface_state_rejects_stale_revision_and_unsupported_field(self) -> None:
        document = SurfaceDocument(
            surface_id="panel-state",
            domain_id="education",
            revision=1,
            components=(ComponentNode(
                id="1", type="image", layout=GridRect(1, 1, 4, 4),
                props={"asset_id": "cat"}, state={"visibility": "visible"},
            ),),
            anchors=(AnchorBinding("a", "1", "image", ("highlight",)),),
        )
        self.orchestrator._active_panels["s1"] = ActivePanelState(document=document, purpose="Bài test")  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ValueError, "base_revision"):
            self.orchestrator.update_surface_state(
                session_id="s1", surface_id="panel-state", base_revision=2,
                updates=[{"anchor_id": "a", "changes": {"visibility": "hidden"}}],
            )
        with self.assertRaisesRegex(ValueError, "does not allow"):
            self.orchestrator.update_surface_state(
                session_id="s1", surface_id="panel-state", base_revision=1,
                updates=[{"anchor_id": "a", "changes": {"flipped": True}}],
            )

    def test_replacement_panel_increments_revision(self) -> None:
        for intent in ("Cho bé xem chó và mèo.", "So sánh hai bạn."):
            result = asyncio.run(self.orchestrator.execute_tool_call_result(
                session_id="s1",
                tool_name="route_request",
                arguments={"domain_id": "education", "intent": intent},
            ))
            self.assertEqual(result.response["status"], "completed")
        self.assertEqual(self.orchestrator.active_panel("s1").revision, 2)

    def test_route_applies_patch_atomically_and_preserves_existing_anchor_identity(self) -> None:
        asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Hiển thị chó."},
        ))
        before = self.orchestrator.active_panel("s1")
        assert before is not None
        self.agent.command = PatchSurfacePlan(
            surface_id=before.document.surface_id,
            base_revision=before.revision,
            operations=(UpdatePropsOperation(anchor_id="b", changes={"asset_id": "cat", "label": "Mèo"}),),
        )

        result = asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Đổi hình chó thành mèo."},
        ))

        self.assertEqual(result.response["status"], "completed")
        after = self.orchestrator.active_panel("s1")
        assert after is not None
        self.assertEqual(after.document.surface_id, before.document.surface_id)
        self.assertEqual(after.revision, before.revision + 1)
        self.assertEqual(after.document.anchor_map["b"].component_id, before.document.anchor_map["b"].component_id)
        self.assertEqual(after.document.component_map[after.document.anchor_map["b"].component_id].props["asset_id"], "cat")

    def test_surface_operations_keep_revision_and_stage_map_in_sync(self) -> None:
        """Every structural/state operation exposes the map of its exact SurfaceDocument."""

        created = asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Hiển thị chó."},
        ))
        active = self.orchestrator.active_panel("s1")
        assert active is not None
        self.assertEqual(created.response["revision"], 1)
        self.assertEqual(created.response["visual_stage_map"], self._stage_map(active.document))

        self.agent.command = PatchSurfacePlan(
            surface_id=active.document.surface_id,
            base_revision=active.revision,
            operations=(UpdatePropsOperation(anchor_id="b", changes={"asset_id": "cat", "label": "Mèo"}),),
        )
        patched = asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Đổi hình chó thành mèo."},
        ))
        active = self.orchestrator.active_panel("s1")
        assert active is not None
        self.assertEqual(patched.response["revision"], 2)
        self.assertEqual(patched.response["visual_stage_map"], self._stage_map(active.document))

        updated = self.orchestrator.update_surface_state(
            session_id="s1",
            surface_id=active.document.surface_id,
            base_revision=active.revision,
            updates=[{"anchor_id": "b", "changes": {"visibility": "hidden"}}],
        )
        active = self.orchestrator.active_panel("s1")
        assert active is not None
        self.assertEqual(updated.response["revision"], 3)
        self.assertEqual(updated.response["visual_stage_map"], self._stage_map(active.document))
        self.assertEqual(updated.panel_update["surface"]["revision"], 3)

        deleted = self.orchestrator.delete_surface(
            session_id="s1",
            surface_id=active.document.surface_id,
            base_revision=active.revision,
        )
        self.assertEqual(deleted.response["revision"], 4)
        self.assertIn("KHÔNG CÓ PANEL", deleted.response["visual_stage_map"])
        self.assertIsNone(self.orchestrator.active_panel("s1"))

    def test_replace_children_preserves_choice_identity_and_recompiles_children(self) -> None:
        self.agent.command = CreateSurfacePlan(
            blocks=(
                _plan_block("text", 1, 1, 16, 1, {"content": "Chọn hình đúng", "role": "title"}),
                PlanBlock(
                    widget_id="choice",
                    grid=GridRect(2, 3, 6, 5),
                    children=(ChoiceChild(widget_id="image", props={"asset_id": "dog"}),),
                ),
            ),
        )
        asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Chọn con vật."},
        ))
        before = self.orchestrator.active_panel("s1")
        assert before is not None
        self.agent.command = PatchSurfacePlan(
            surface_id=before.document.surface_id,
            base_revision=before.revision,
            operations=(ReplaceChildrenOperation(
                anchor_id="b",
                children=(ChoiceChild(widget_id="image", props={"asset_id": "cat"}),),
            ),),
        )

        result = asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Câu hỏi tiếp theo."},
        ))

        self.assertEqual(result.response["status"], "completed")
        after = self.orchestrator.active_panel("s1")
        assert after is not None
        self.assertEqual(after.revision, before.revision + 1)
        self.assertEqual(after.document.anchor_map["b"].component_id, before.document.anchor_map["b"].component_id)
        choice = after.document.component_map[after.document.anchor_map["b"].component_id]
        self.assertEqual(choice.children[0].type, "image")
        self.assertEqual(choice.children[0].props["asset_id"], "cat")

    def _stage_map(self, document):
        resources = self.registry.load(document.domain_id)
        return render_visual_stage_map(
            document,
            widget_registry=self.orchestrator._panel_compiler.widget_registry,  # type: ignore[attr-defined]
            asset_catalog=resources.assets,
        )

    def test_invalid_patch_does_not_replace_the_active_surface(self) -> None:
        asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Hiển thị chó."},
        ))
        before = self.orchestrator.active_panel("s1")
        assert before is not None
        self.agent.command = PatchSurfacePlan(
            surface_id=before.document.surface_id,
            base_revision=before.revision,
            operations=(
                # Title occupies row 1; moving the image there makes the whole
                # candidate invalid and must leave the active surface untouched.
                MoveBlockOperation(anchor_id="b", grid=GridRect(1, 1, 6, 5)),
            ),
        )
        result = asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Sửa bố cục."},
        ))

        self.assertEqual(result.response["status"], "error")
        after = self.orchestrator.active_panel("s1")
        assert after is not None
        self.assertEqual(after.document, before.document)
        self.assertEqual(after.revision, before.revision)

    def test_two_cat_bindings_materialize_the_same_layout_with_two_cat_images(self) -> None:
        self.agent.command = CreateSurfacePlan(
            blocks=(
                _plan_block("text", 1, 1, 16, 1, {"content": "Cùng quan sát hai bạn mèo nhé!", "role": "title"}),
                _plan_block("image", 1, 3, 6, 5, {"asset_id": "cat", "label": "Mèo 1"}),
                _plan_block("image", 10, 3, 6, 5, {"asset_id": "cat", "label": "Mèo 2"}),
            ),
        )
        result = asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Hiển thị hai con mèo."},
        ))

        self.assertEqual(result.response["status"], "completed")
        document = self.orchestrator.active_panel("s1").document
        self.assertEqual(document.components[1].props["asset_id"], "cat")
        self.assertEqual(document.components[2].props["asset_id"], "cat")

    def test_route_returns_compiler_feedback_to_plan_agent_then_retries(self) -> None:
        compiler = _RepairingCompiler()
        orchestrator = LiveSessionOrchestrator(
            domain_registry=self.registry,
            plan_agent=self.agent,  # type: ignore[arg-type]
            panel_compiler=compiler,  # type: ignore[arg-type]
        )

        result = asyncio.run(orchestrator.execute_tool_call_result(
            session_id="s-repair",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Hiển thị hai con vật."},
        ))

        self.assertEqual(result.response["status"], "completed")
        self.assertEqual(compiler.calls, 2)
        self.assertEqual(len(self.agent.requests), 2)
        feedback = self.agent.requests[1].validation_feedback
        self.assertIsNotNone(feedback)
        assert feedback is not None
        self.assertEqual(feedback["error_code"], "grid_overlap")
        self.assertEqual(feedback["details"]["overlap_cells"], [{"col": 7, "row": 6}])

    def test_unknown_domain_is_returned_as_a_safe_error(self) -> None:
        result = asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "missing", "intent": "x"},
        ))
        self.assertEqual(result.response["status"], "error")
        self.assertIsNone(result.presentation)


if __name__ == "__main__":
    unittest.main()
