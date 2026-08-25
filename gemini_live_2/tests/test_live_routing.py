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
    DataBundle,
    GridRect,
    PanelBlock,
    PanelCompilationError,
    PanelCompiler,
    PanelIR,
)
from gemini_live_2.plan_agent import PlanAgentResult, UseExistingPlanDecision
from gemini_live_2.settings import Settings
from gemini_live_2.widgets import build_default_widget_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _PlanAgentStub:
    def __init__(self) -> None:
        self.requests = []
        self.decision = UseExistingPlanDecision(
            "two_subject_comparison",
            bindings={
                "$block_1_content": "Cùng quan sát chó và mèo nhé!",
                "$block_2_asset_id": "dog",
                "$block_2_label": "Chó",
                "$block_3_asset_id": "cat",
                "$block_3_label": "Mèo",
            },
        )

    async def plan(self, request):
        self.requests.append(request)
        return PlanAgentResult(
            decision=self.decision,
            data_bundle=DataBundle(domain_id=request.domain_id, data={}),
        )


class _RepairingCompiler:
    """Reject once, then delegate to the real compiler."""

    def __init__(self) -> None:
        self.widget_registry = build_default_widget_registry()
        self._compiler = PanelCompiler(self.widget_registry)
        self.calls = 0

    def compile(self, **kwargs):
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
        return self._compiler.compile(**kwargs)


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
        self.assertEqual(result.presentation.panel["ui_type"], "panel_ir")
        self.assertEqual(result.presentation.panel["revision"], 1)
        self.assertIn("cô giáo thân thiện", result.response["presentation_instruction"])
        self.assertEqual(self.orchestrator.active_panel("s1").revision, 1)
        self.assertEqual(self.agent.requests[0].recent_history[0]["text"], "Cho bé xem hai con vật")

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
        self.assertIn(":block:2:anchor:image", cue["target_id"])
        with self.assertRaisesRegex(ValueError, "unknown anchor_id"):
            self.orchestrator.present_visual(session_id="s1", anchor_id="missing", effect_id="highlight")

    def test_panel_action_reveals_hidden_blocks_in_place_and_rejects_repeat(self) -> None:
        panel = PanelIR(
            panel_id="panel-hidden",
            domain_id="education",
            blocks=(
                PanelBlock(
                    id="1",
                    widget_id="image",
                    grid=GridRect(col=1, row=1, col_span=4, row_span=4),
                    props={"asset_id": "cat", "label": "MÃ¨o"},
                    visibility="hidden",
                ),
            ),
            anchors=(
                AnchorBinding(
                    anchor_id="a",
                    block_id="1",
                    anchor_key="image",
                    target_id="panel:panel-hidden:block:1:anchor:image",
                    allowed_effect_ids=("highlight", "circle"),
                ),
            ),
        )
        self.orchestrator._active_panels["s1"] = ActivePanelState(panel_ir=panel)  # type: ignore[attr-defined]

        result = self.orchestrator.panel_action(
            session_id="s1", action_id="reveal", anchor_ids=["a"],
        )

        updated = self.orchestrator.active_panel("s1")
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.panel_ir.panel_id, "panel-hidden")
        self.assertEqual(updated.panel_ir.blocks[0].visibility, "visible")
        self.assertEqual(result.response["revision"], 2)
        self.assertEqual(result.panel_update["revision"], 2)
        self.assertEqual(result.panel_update["panel"]["blocks"][0]["props"]["asset_id"], "cat")
        self.assertEqual(
            self.orchestrator.present_visual(session_id="s1", anchor_id="a", effect_id="highlight")["target_id"],
            "panel:panel-hidden:block:1:anchor:image",
        )
        with self.assertRaisesRegex(ValueError, "currently hidden"):
            self.orchestrator.panel_action(session_id="s1", action_id="reveal", anchor_ids=["a"])

    def test_panel_action_rejects_unknown_or_duplicate_anchors_without_changing_state(self) -> None:
        panel = PanelIR(
            panel_id="panel-hidden",
            domain_id="education",
            blocks=(
                PanelBlock(
                    id="1", widget_id="image", grid=GridRect(1, 1, 4, 4),
                    props={"asset_id": "cat"}, visibility="hidden",
                ),
            ),
            anchors=(AnchorBinding("a", "1", "image", "panel:panel-hidden:block:1:anchor:image", ("highlight",)),),
        )
        self.orchestrator._active_panels["s1"] = ActivePanelState(panel_ir=panel)  # type: ignore[attr-defined]
        with self.assertRaisesRegex(ValueError, "unknown anchor_id"):
            self.orchestrator.panel_action(session_id="s1", action_id="reveal", anchor_ids=["missing"])
        with self.assertRaisesRegex(ValueError, "duplicates"):
            self.orchestrator.panel_action(session_id="s1", action_id="reveal", anchor_ids=["a", "a"])
        self.assertEqual(self.orchestrator.active_panel("s1").revision, 1)  # type: ignore[union-attr]

    def test_panel_action_reveals_multiple_hidden_blocks_together(self) -> None:
        panel = PanelIR(
            panel_id="panel-hidden",
            domain_id="education",
            blocks=(
                PanelBlock("1", "image", GridRect(1, 1, 4, 4), {"asset_id": "cat"}, "hidden"),
                PanelBlock("2", "answer", GridRect(6, 1, 2, 2), {"value": "3"}, "hidden"),
            ),
            anchors=(
                AnchorBinding("a", "1", "image", "panel:panel-hidden:block:1:anchor:image", ("highlight",)),
                AnchorBinding("b", "2", "answer", "panel:panel-hidden:block:2:anchor:answer", ("circle",)),
            ),
        )
        self.orchestrator._active_panels["s1"] = ActivePanelState(panel_ir=panel)  # type: ignore[attr-defined]

        result = self.orchestrator.panel_action(
            session_id="s1", action_id="reveal", anchor_ids=["a", "b"],
        )

        updated = self.orchestrator.active_panel("s1")
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual([block.visibility for block in updated.panel_ir.blocks], ["visible", "visible"])
        self.assertEqual(result.response["anchor_ids"], ["a", "b"])

    def test_replacement_panel_increments_revision(self) -> None:
        for intent in ("Cho bé xem chó và mèo.", "So sánh hai bạn."):
            result = asyncio.run(self.orchestrator.execute_tool_call_result(
                session_id="s1",
                tool_name="route_request",
                arguments={"domain_id": "education", "intent": intent},
            ))
            self.assertEqual(result.response["status"], "completed")
        self.assertEqual(self.orchestrator.active_panel("s1").revision, 2)

    def test_two_cat_bindings_materialize_the_same_layout_with_two_cat_images(self) -> None:
        self.agent.decision = UseExistingPlanDecision(
            "two_subject_comparison",
            bindings={
                "$block_1_content": "Cùng quan sát hai bạn mèo nhé!",
                "$block_2_asset_id": "cat",
                "$block_2_label": "Mèo 1",
                "$block_3_asset_id": "cat",
                "$block_3_label": "Mèo 2",
            },
        )
        result = asyncio.run(self.orchestrator.execute_tool_call_result(
            session_id="s1",
            tool_name="route_request",
            arguments={"domain_id": "education", "intent": "Hiển thị hai con mèo."},
        ))

        self.assertEqual(result.response["status"], "completed")
        panel = self.orchestrator.active_panel("s1").panel_ir
        self.assertEqual(panel.blocks[1].props["asset_id"], "cat")
        self.assertEqual(panel.blocks[2].props["asset_id"], "cat")

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
