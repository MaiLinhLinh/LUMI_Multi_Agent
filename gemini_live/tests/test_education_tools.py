"""Tests for trusted arithmetic data used by the Education domain."""

from __future__ import annotations

import asyncio
import json
import unittest

from gemini_live.domains import DomainRequest
from gemini_live.domains.education import EducationLiveDomain
from gemini_live.domains.education.adapter import EducationPresentationAdapter
from gemini_live.domains.education.tools import EducationTools, ExerciseValidationError
from gemini_live.domains.registry import LiveDomainRegistry
from gemini_live.live.dispatcher import LiveToolDispatcher
from gemini_live.live.orchestrator import LiveSessionOrchestrator
from gemini_live.live.scene_state import LivePresentation
from gemini_live.presentation.capabilities import load_template_metadata, presentation_capabilities
from gemini_live.presentation.contract_compiler import compile_presentation_plan
from gemini_live.presentation.planner_schemas import PresentationPlan, PresentationStep
from gemini_live.presentation.pipeline import PresentationPipeline


class _FixedChoice:
    def choice(self, _sequence: object) -> tuple[str, str]:
        return ("ball", "quả bóng")


class _PlannerStub:
    """Deterministic stand-in for Gemini while testing the domain contract."""

    def generate_structured(self, **kwargs: object) -> dict[str, object]:
        payload = json.loads(str(kwargs["user_text"]))
        left_group = next(item for item in payload["grounded_facts"] if item["id"] == "left_group")
        count = left_group["value"]["count"]
        asset_label = left_group["value"]["asset_label"]
        return {
            "data": {
                "schema_version": "presentation_plan.v1",
                "steps": [
                    {
                        "narration": "Cùng quan sát bài toán nhé.",
                        "fact_id": "exercise_overview",
                        "effect": "reveal",
                        "gesture": "explain",
                    },
                    {
                        "narration": f"Bên trái có {count} {asset_label}.",
                        "fact_id": "left_group",
                        "effect": "draw_circle",
                        "gesture": "point_left",
                    },
                    {
                        "narration": "Ba cộng hai bằng năm.",
                        "fact_id": "answer",
                        "effect": "reveal",
                        "gesture": "explain",
                    },
                ],
            },
            "usage": {},
        }


class EducationToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = EducationTools(choice_source=_FixedChoice())

    def test_addition_is_computed_by_code(self) -> None:
        exercise = self.tools.create_math_exercise(
            {"operation": "+", "left_operand": 3, "right_operand": 2}
        )
        self.assertEqual(exercise.result, 5)
        self.assertEqual(exercise.asset_id, "ball")
        self.assertEqual(exercise.to_view_data()["left_count"], 3)

    def test_subtraction_is_computed_by_code(self) -> None:
        exercise = self.tools.create_math_exercise(
            {"operation": "-", "left_operand": 8, "right_operand": 3}
        )
        self.assertEqual(exercise.result, 5)

    def test_supports_a_larger_number_range(self) -> None:
        exercise = self.tools.create_math_exercise(
            {"operation": "+", "left_operand": 58, "right_operand": 42}
        )
        self.assertEqual(exercise.result, 100)

    def test_rejects_negative_operand(self) -> None:
        with self.assertRaises(ExerciseValidationError):
            self.tools.create_math_exercise(
                {"operation": "+", "left_operand": -1, "right_operand": 2}
            )

    def test_rejects_negative_subtraction(self) -> None:
        with self.assertRaises(ExerciseValidationError):
            self.tools.create_math_exercise(
                {"operation": "-", "left_operand": 2, "right_operand": 7}
            )

    def test_live_domain_exposes_the_tool_and_verified_result(self) -> None:
        domain = EducationLiveDomain()
        self.assertEqual(domain.tool_declarations[0]["name"], "create_math_exercise")
        result = asyncio.run(domain.execute_tool(
            "create_math_exercise",
            {"operation": "+", "left_operand": 7, "right_operand": 3},
            request=DomainRequest(query="Dạy bé cộng"),
            context={},
        ))
        self.assertEqual(result.tool_response["exercise"]["result"], 10)
        self.assertEqual(result.context["last_exercise"]["left_count"], 7)
        self.assertIsNotNone(result.presentation)

    def test_object_group_facts_compile_only_to_template_targets(self) -> None:
        metadata = load_template_metadata("education", "object_group_math")
        capabilities = presentation_capabilities(metadata)
        data = {
            "template_id": "object_group_math",
            "asset_label": "bông hoa",
            "left_count": 3,
            "right_count": 2,
            "operator": "+",
            "result": 5,
        }
        adapter = EducationPresentationAdapter()
        facts = adapter.build_candidate_facts(
            data, compact_data=data, presentation_capabilities=capabilities
        )
        self.assertEqual(
            [fact.id for fact in facts],
            ["exercise_overview", "left_group", "operator", "right_group", "expression", "result_items", "answer"],
        )
        compiled = compile_presentation_plan(
            PresentationPlan(steps=[PresentationStep(
                narration="Ba cộng hai bằng năm.", fact_id="answer", effect="reveal"
            )]),
            template_metadata=metadata,
            compact_data=data,
            target_resolver=adapter.resolve_target,
            grounded_facts=facts,
        )
        self.assertEqual(compiled.steps[0].target_id, "math.result.number")

    def test_education_tool_reaches_shared_pipeline_and_scene_state(self) -> None:
        registry = LiveDomainRegistry()
        registry.register(EducationLiveDomain())
        orchestrator = LiveSessionOrchestrator(
            LiveToolDispatcher(registry),
            presentation_pipeline=PresentationPipeline(planner_runtime=_PlannerStub()),
        )
        result = asyncio.run(orchestrator.execute_tool_call_result(
            session_id="education-integration",
            query="Dạy bé phép cộng",
            tool_name="create_math_exercise",
            arguments={"operation": "+", "left_operand": 3, "right_operand": 2},
        ))
        self.assertEqual(result.tool_response["status"], "completed")
        self.assertIn("facts", result.tool_response)
        self.assertEqual(result.tool_response["presentation"]["template_id"], "object_group_math")
        self.assertIsInstance(result.presentation, LivePresentation)
        scene = result.presentation.scenes.resolve("education-scene-2")
        self.assertEqual(scene["target_id"], "math.group.a")
        self.assertEqual(scene["effect"], "draw_circle")


if __name__ == "__main__":
    unittest.main()
