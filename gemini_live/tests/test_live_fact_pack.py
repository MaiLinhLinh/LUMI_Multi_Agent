"""Tests for ASCII-stage presentation and server-side visual validation."""

from __future__ import annotations

import asyncio
import unittest

from gemini_live.domains.education import EducationLiveDomain
from gemini_live.domains.education.adapter import EducationPresentationAdapter
from gemini_live.domains.weather.prompt import WEATHER_PRESENTATION_INSTRUCTION
from gemini_live.domains.registry import LiveDomainRegistry
from gemini_live.live.dispatcher import LiveToolDispatcher
from gemini_live.live.orchestrator import LiveSessionOrchestrator
from gemini_live.presentation.pipeline import PresentationPipeline, PresentationRequest


class LiveStageMapTests(unittest.TestCase):
    def test_domain_presentation_prompts_remain_in_prompt_modules(self) -> None:
        self.assertIn("present_visual", WEATHER_PRESENTATION_INSTRUCTION)
        self.assertIn("present_visual", EducationPresentationAdapter().live_presentation_instruction())

    def test_stage_map_contains_education_phase_state(self) -> None:
        template_id = "object_group_math"
        data = {
            "template_id": template_id,
            "asset_label": "bông hoa",
            "left_count": 3,
            "right_count": 2,
            "operator": "+",
            "result": 5,
        }
        prepared = PresentationPipeline().prepare(request=PresentationRequest(
            domain_id="education",
            template_id=template_id,
            view_model=data,
            adapter=EducationPresentationAdapter(presentation_phase="incorrect_hint"),
            domain_data=data,
            compact_data=data,
        ))

        self.assertIn("MỤC TIÊU LƯỢT NÀY", prepared.visual_stage_map)
        self.assertIn("Do not reveal or imply the result", prepared.visual_stage_map)
        self.assertIn("chưa được phép công bố", prepared.visual_stage_map)

    def test_presentation_pack_keeps_dom_targets_server_only(self) -> None:
        template_id = "object_group_math"
        data = {
            "template_id": template_id,
            "asset_label": "bông hoa",
            "left_count": 3,
            "right_count": 2,
            "operator": "+",
            "result": 5,
        }
        request = PresentationRequest(
            domain_id="education",
            template_id=template_id,
            view_model=data,
            adapter=EducationPresentationAdapter(),
            domain_data=data,
            compact_data=data,
        )
        prepared = PresentationPipeline().prepare(request=request)
        pack = PresentationPipeline.build_live_presentation_pack(prepared)

        self.assertTrue(pack.panel_anchor_map)
        self.assertEqual(pack.panel_anchor_map["e"]["target_id"], "math.result.number")
        self.assertTrue(any(effect["id"] == "circle" for effect in pack.supported_effects))

    def test_orchestrator_sends_no_facts_and_validates_anchor(self) -> None:
        registry = LiveDomainRegistry()
        registry.register(EducationLiveDomain())
        orchestrator = LiveSessionOrchestrator(
            LiveToolDispatcher(registry), presentation_pipeline=PresentationPipeline()
        )
        result = asyncio.run(orchestrator.execute_tool_call_result(
            session_id="stage-map-presentation",
            query="Cho tôi phép cộng.",
            tool_name="create_arithmetic_exercise",
            arguments={"operation": "+", "left_operand": 3, "right_operand": 2},
        ))

        self.assertNotIn("facts", result.response)
        self.assertNotIn("interaction_instruction", result.response)
        self.assertNotIn("presentation", result.response)
        self.assertIn("visual_stage_map", result.response)
        self.assertIn("visual_effects", result.response)
        self.assertIn("presentation_instruction", result.response)
        cue = orchestrator.present_visual(
            session_id="stage-map-presentation", anchor_id="a", effect_id="highlight"
        )
        self.assertEqual(cue["target_id"], "math.group.a")


if __name__ == "__main__":
    unittest.main()
