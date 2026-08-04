"""Tests for the single active Planner-to-Compiler contract."""

from __future__ import annotations

import unittest

from gemini_live.presentation.contract_compiler import (
    PresentationCompileError,
    compile_presentation_plan,
)
from gemini_live.presentation.planner_schemas import GroundedFact, PresentationPlan, PresentationStep


class PresentationPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fact = GroundedFact(
            id="rain-risk",
            metric="rain_probability",
            operation="summary",
            value=96,
            unit="%",
            focus="rain_risk",
            effect_hint="draw_circle",
        )

    @staticmethod
    def _metadata() -> dict:
        return {
            "presentation_capabilities": {
                "overview": {"target_id": "weather.overview", "allowed_effects": ["highlight"]},
                "rain_risk": {"target_id": "weather.rain", "allowed_effects": ["draw_circle", "highlight"]},
            }
        }

    @staticmethod
    def _resolver(capability: dict | None, entity: dict, compact_data: dict) -> str | None:
        return capability.get("target_id") if capability else None

    def test_compiles_grounded_scene(self) -> None:
        contract = compile_presentation_plan(
            PresentationPlan(steps=[PresentationStep(
                fact_id="rain-risk",
                narration="Khả năng mưa là 96 phần trăm.",
                effect="draw_circle",
            )]),
            template_metadata=self._metadata(),
            compact_data={},
            target_resolver=self._resolver,
            grounded_facts=[self.fact],
        )
        self.assertEqual(contract.steps[0].target_id, "weather.rain")

    def test_rejects_unknown_fact(self) -> None:
        with self.assertRaises(PresentationCompileError):
            compile_presentation_plan(
                PresentationPlan(steps=[PresentationStep(
                    fact_id="unknown-fact", narration="Mưa.", effect="draw_circle",
                )]),
                template_metadata=self._metadata(),
                compact_data={},
                target_resolver=self._resolver,
                grounded_facts=[self.fact],
            )


if __name__ == "__main__":
    unittest.main()
