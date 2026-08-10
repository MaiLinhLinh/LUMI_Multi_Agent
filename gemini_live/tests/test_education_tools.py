"""Tests for trusted arithmetic data used by the Education domain."""

from __future__ import annotations

import asyncio
import json
import unittest

from gemini_live.domains import DomainRequest
from gemini_live.domains.education import EducationLiveDomain
from gemini_live.domains.education.adapter import EducationPresentationAdapter
from gemini_live.domains.education.context import EducationContextResolver
from gemini_live.domains.education.tools import EducationTools, ExerciseValidationError
from gemini_live.domains.registry import LiveDomainRegistry
from gemini_live.live.dispatcher import LiveToolDispatcher
from gemini_live.live.orchestrator import LiveSessionOrchestrator
from gemini_live.live.visual_presentation import RenderedPresentation
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
        facts = {item["id"]: item for item in payload["grounded_facts"]}
        if "result_items" in facts:
            result = facts["answer"]["value"]["result"]
            asset_label = facts["answer"]["value"]["asset_label"]
            steps = [
                {
                    "narration": f"Chính xác rồi, còn {result} {asset_label}.",
                    "fact_id": "result_items",
                    "effect": "reveal_items",
                    "gesture": "explain",
                },
                {
                    "narration": f"Đáp án là {result}.",
                    "fact_id": "answer",
                    "effect": "reveal",
                    "gesture": "explain",
                },
            ]
        else:
            left_group = facts["left_group"]
            count = left_group["value"]["count"]
            asset_label = left_group["value"]["asset_label"]
            steps = [
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
                    "narration": "Con thử tính xem bằng bao nhiêu nhé.",
                    "fact_id": "expression",
                    "effect": "highlight",
                    "gesture": "explain",
                },
            ]
        return {
            "data": {
                "schema_version": "presentation_plan.v1",
                "steps": steps,
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
        self.assertEqual(domain.tool_declarations[1]["name"], "check_child_answer")
        result = asyncio.run(domain.execute_tool(
            "create_math_exercise",
            {"operation": "+", "left_operand": 7, "right_operand": 3},
            request=DomainRequest(query="Dạy bé cộng"),
            context={},
        ))
        self.assertEqual(result.tool_response["exercise"]["result"], 10)
        self.assertEqual(result.context["last_exercise"]["left_count"], 7)
        state = EducationContextResolver.lesson_state(result.context)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.correct_answer, 10)
        self.assertEqual(state.phase, "awaiting_answer")
        self.assertEqual(result.context["lesson_phase"], "awaiting_answer")
        self.assertIsNotNone(result.presentation)

    def test_check_child_answer_is_code_verified_and_reveals_after_two_mistakes(self) -> None:
        domain = EducationLiveDomain()
        created = asyncio.run(domain.execute_tool(
            "create_math_exercise",
            {"operation": "-", "left_operand": 9, "right_operand": 4},
            request=DomainRequest(query="Dạy bé phép trừ"),
            context={},
        ))
        first_wrong = asyncio.run(domain.execute_tool(
            "check_child_answer",
            {"answer": 2},
            request=DomainRequest(query="Bằng hai"),
            context=created.context,
        ))
        self.assertEqual(first_wrong.tool_response["status"], "incorrect_hint")
        self.assertEqual(first_wrong.tool_response["attempt_count"], 1)
        self.assertNotIn("correct_answer", first_wrong.tool_response)

        reveal = asyncio.run(domain.execute_tool(
            "check_child_answer",
            {"answer": 2},
            request=DomainRequest(query="Con đoán hai"),
            context=first_wrong.context,
        ))
        self.assertEqual(reveal.tool_response["status"], "reveal_answer")
        self.assertEqual(reveal.tool_response["correct_answer"], 5)
        self.assertEqual(reveal.tool_response["phase"], "completed")
        self.assertIsNotNone(reveal.presentation)

    def test_check_child_answer_accepts_the_verified_answer(self) -> None:
        domain = EducationLiveDomain()
        created = asyncio.run(domain.execute_tool(
            "create_math_exercise",
            {"operation": "+", "left_operand": 3, "right_operand": 2},
            request=DomainRequest(query="Dạy bé phép cộng"),
            context={},
        ))
        checked = asyncio.run(domain.execute_tool(
            "check_child_answer",
            {"answer": 5},
            request=DomainRequest(query="Bằng năm"),
            context=created.context,
        ))
        self.assertEqual(checked.tool_response["status"], "correct")
        self.assertEqual(checked.tool_response["correct_answer"], 5)
        self.assertIsNotNone(checked.presentation)

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
            ["exercise_overview", "left_group", "operator", "right_group", "expression"],
        )
        compiled = compile_presentation_plan(
            PresentationPlan(steps=[PresentationStep(
                narration="Ba cộng hai bằng bao nhiêu nhỉ?", fact_id="expression", effect="highlight"
            )]),
            template_metadata=metadata,
            compact_data=data,
            target_resolver=adapter.resolve_target,
            grounded_facts=facts,
        )
        self.assertEqual(compiled.steps[0].target_id, "math.expression")

    def test_fallback_plan_asks_for_the_answer_without_revealing_it(self) -> None:
        metadata = load_template_metadata("education", "object_group_math")
        capabilities = presentation_capabilities(metadata)
        data = {
            "template_id": "object_group_math",
            "asset_label": "bông hoa",
            "left_count": 9,
            "right_count": 4,
            "operator": "-",
            "result": 5,
        }
        adapter = EducationPresentationAdapter()
        facts = adapter.build_candidate_facts(
            data, compact_data=data, presentation_capabilities=capabilities
        )
        fallback = adapter.fallback_plan(data, capabilities, facts)
        self.assertEqual(fallback.steps[0].fact_id, "expression")
        self.assertIn("9 - 4 bằng bao nhiêu", fallback.steps[0].narration)
        self.assertNotIn("5", fallback.steps[0].narration)

    def test_education_tool_reaches_shared_fact_pipeline(self) -> None:
        registry = LiveDomainRegistry()
        registry.register(EducationLiveDomain())
        orchestrator = LiveSessionOrchestrator(
            LiveToolDispatcher(registry),
            presentation_pipeline=PresentationPipeline(),
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
        self.assertEqual(result.tool_response["presentation"]["mode"], "fact_pack")
        self.assertIsInstance(result.presentation, RenderedPresentation)

    def test_correct_answer_reaches_shared_pipeline_with_result_reveal(self) -> None:
        registry = LiveDomainRegistry()
        registry.register(EducationLiveDomain())
        orchestrator = LiveSessionOrchestrator(
            LiveToolDispatcher(registry),
            presentation_pipeline=PresentationPipeline(),
        )
        session_id = "education-correct-answer"
        asyncio.run(orchestrator.execute_tool_call_result(
            session_id=session_id,
            query="Dạy bé phép trừ",
            tool_name="create_math_exercise",
            arguments={"operation": "-", "left_operand": 9, "right_operand": 4},
        ))
        checked = asyncio.run(orchestrator.execute_tool_call_result(
            session_id=session_id,
            query="Bằng năm",
            tool_name="check_child_answer",
            arguments={"answer": 5},
        ))
        self.assertEqual(checked.tool_response["status"], "correct")
        self.assertIsInstance(checked.presentation, RenderedPresentation)


if __name__ == "__main__":
    unittest.main()
