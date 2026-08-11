"""Tests for trusted arithmetic data used by the Education domain."""

from __future__ import annotations

import asyncio
import unittest

from gemini_live.domains import DomainRequest
from gemini_live.domains.education import EducationLiveDomain
from gemini_live.domains.education.context import EducationContextResolver
from gemini_live.domains.education.tools import EducationTools, ExerciseValidationError
from gemini_live.domains.registry import LiveDomainRegistry
from gemini_live.live.dispatcher import LiveToolDispatcher
from gemini_live.live.orchestrator import LiveSessionOrchestrator
from gemini_live.live.visual_presentation import RenderedPresentation
from gemini_live.presentation.pipeline import PresentationPipeline


class _FixedChoice:
    def choice(self, _sequence: object) -> tuple[str, str]:
        return ("ball", "quả bóng")


class EducationToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = EducationTools(choice_source=_FixedChoice())

    def test_arithmetic_is_computed_by_code(self) -> None:
        cases = (("+", 3, 2, 5), ("-", 8, 3, 5), ("*", 3, 4, 12), ("/", 12, 3, 4))
        for operation, left, right, expected in cases:
            with self.subTest(operation=operation):
                exercise = self.tools.create_arithmetic_exercise(
                    {"operation": operation, "left_operand": left, "right_operand": right}
                )
                self.assertEqual(exercise.result, expected)

    def test_rejects_invalid_division(self) -> None:
        for arguments in (
            {"operation": "/", "left_operand": 12, "right_operand": 0},
            {"operation": "/", "left_operand": 10, "right_operand": 3},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ExerciseValidationError):
                self.tools.create_arithmetic_exercise(arguments)

    def test_create_tool_stores_a_verified_open_exercise(self) -> None:
        domain = EducationLiveDomain()
        result = asyncio.run(domain.execute_tool(
            "create_arithmetic_exercise",
            {"operation": "+", "left_operand": 7, "right_operand": 3},
            request=DomainRequest(query="Dạy bé cộng"),
            context={},
        ))
        self.assertEqual(result.status, "completed")
        state = EducationContextResolver.lesson_state(result.context)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.correct_answer, 10)
        self.assertIsNotNone(result.presentation)

    def test_check_answer_is_code_verified(self) -> None:
        domain = EducationLiveDomain()
        created = asyncio.run(domain.execute_tool(
            "create_arithmetic_exercise",
            {"operation": "-", "left_operand": 9, "right_operand": 4},
            request=DomainRequest(query="Dạy bé phép trừ"),
            context={},
        ))
        first_wrong = asyncio.run(domain.execute_tool(
            "check_child_answer", {"answer": 2},
            request=DomainRequest(query="Bằng hai"), context=created.context,
        ))
        self.assertEqual(first_wrong.status, "incorrect_hint")
        reveal = asyncio.run(domain.execute_tool(
            "check_child_answer", {"answer": 2},
            request=DomainRequest(query="Con đoán hai"), context=first_wrong.context,
        ))
        self.assertEqual(reveal.status, "reveal_answer")

    def test_education_tool_reaches_live_fact_pipeline(self) -> None:
        registry = LiveDomainRegistry()
        registry.register(EducationLiveDomain())
        orchestrator = LiveSessionOrchestrator(
            LiveToolDispatcher(registry), presentation_pipeline=PresentationPipeline()
        )
        result = asyncio.run(orchestrator.execute_tool_call_result(
            session_id="education-integration",
            query="Dạy bé phép cộng",
            tool_name="create_arithmetic_exercise",
            arguments={"operation": "+", "left_operand": 3, "right_operand": 2},
        ))
        self.assertEqual(result.response["status"], "completed")
        self.assertIn("facts", result.response)
        self.assertIsInstance(result.presentation, RenderedPresentation)


if __name__ == "__main__":
    unittest.main()
