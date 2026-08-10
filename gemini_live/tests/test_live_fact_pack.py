"""Tests for compact, server-resolved facts prepared for Gemini Live."""

from __future__ import annotations

import asyncio
import unittest

from gemini_live.domains.education import EducationLiveDomain
from gemini_live.domains.education.adapter import EducationPresentationAdapter
from gemini_live.domains.weather.adapter import WeatherPresentationAdapter
from gemini_live.domains.registry import LiveDomainRegistry
from gemini_live.live.dispatcher import LiveToolDispatcher
from gemini_live.live.orchestrator import LiveSessionOrchestrator
from gemini_live.presentation.capabilities import (
    load_template_metadata,
    presentation_capabilities,
)
from gemini_live.presentation.pipeline import PresentationPipeline, PresentationRequest


class LiveFactPackTests(unittest.TestCase):
    def test_weather_adapter_exposes_its_live_presentation_instruction(self) -> None:
        instruction = WeatherPresentationAdapter().live_presentation_instruction()

        self.assertIn("MC thời tiết", instruction)
        self.assertIn("present_visual", instruction)

    def test_education_adapter_exposes_live_instruction_and_interaction_context(self) -> None:
        adapter = EducationPresentationAdapter(presentation_phase="incorrect_hint")

        self.assertIn("present_visual", adapter.live_presentation_instruction())
        context = adapter.live_presentation_context()
        self.assertEqual(context["interaction_mode"], "incorrect_hint")
        self.assertIn("hint", context["interaction_instruction"])

    def test_facts_expose_short_anchors_but_dom_targets_stay_server_only(self) -> None:
        template_id = "object_group_math"
        metadata = load_template_metadata("education", template_id)
        adapter = EducationPresentationAdapter()
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
            adapter=adapter,
            domain_data=data,
            compact_data=data,
        )
        pipeline = PresentationPipeline()
        prepared = pipeline.prepare(request=request)
        pack = pipeline.build_live_fact_pack(request, prepared)

        self.assertEqual([item["id"] for item in pack.facts_for_live], [
            f"f{index}" for index in range(1, len(pack.facts_for_live) + 1)
        ])
        self.assertTrue(pack.anchor_target_map)
        self.assertTrue(any("target_id" in item for item in pack.anchor_target_map.values()))
        self.assertFalse(any("target_id" in item for item in pack.facts_for_live))
        self.assertTrue(any(item["visualizable"] for item in pack.facts_for_live))
        self.assertIn("a", pack.anchor_target_map)
        self.assertTrue(prepared.visual_stage_map.startswith("VISUAL STAGE MAP"))
        self.assertTrue(any(effect["id"] == "circle" for effect in pack.supported_effects))

    def test_server_resolves_visual_anchor_without_exposing_dom_id(self) -> None:
        registry = LiveDomainRegistry()
        registry.register(EducationLiveDomain())
        orchestrator = LiveSessionOrchestrator(
            LiveToolDispatcher(registry), presentation_pipeline=PresentationPipeline()
        )
        result = asyncio.run(orchestrator.execute_tool_call_result(
            session_id="fact-pack-presentation",
            query="Cho tôi phép cộng.",
            tool_name="create_math_exercise",
            arguments={"operation": "+", "left_operand": 3, "right_operand": 2},
        ))
        self.assertIn("visual_stage_map", result.tool_response)
        self.assertIn("[anchor: a]", result.tool_response["visual_stage_map"])
        cue = orchestrator.present_visual(
            session_id="fact-pack-presentation", anchor_id="a", effect_id="highlight"
        )
        self.assertEqual(cue["anchor_id"], "a")
        self.assertEqual(cue["effect"], "highlight")
        self.assertTrue(cue["target_id"].startswith("math."))
        with self.assertRaises(ValueError):
            orchestrator.present_visual(
                session_id="fact-pack-presentation", anchor_id="a", effect_id="reveal"
            )


if __name__ == "__main__":
    unittest.main()
