"""Tests for arithmetic construction in the Education domain."""

from __future__ import annotations

import asyncio
import unittest

from gemini_live.domains import DomainRequest
from gemini_live.domains.education import EducationLiveDomain
from gemini_live.domains.education.tools import EducationTools, ExerciseValidationError
from gemini_live.domains.registry import LiveDomainRegistry
from gemini_live.live.dispatcher import LiveToolDispatcher
from gemini_live.live.orchestrator import LiveSessionOrchestrator
from gemini_live.live.visual_presentation import RenderedPresentation
from gemini_live.presentation.pipeline import PresentationPipeline
from gemini_live.template_engine.template_manager import TemplateResolution


class _FixedChoice:
    def choice(self, _sequence: object) -> tuple[str, str]:
        return ("ball", "ball")


class _EducationTemplateManager:
    async def resolve(
        self,
        request: object,
        *,
        recent_history: tuple[dict[str, str], ...] = (),
    ) -> TemplateResolution:
        del recent_history
        render_data = request.render_data
        template_id = (
            "repeated_groups_arithmetic"
            if render_data.get("operation") in {"*", "/"}
            else "object_group_math"
        )
        return TemplateResolution(decision="use_existing", template_id=template_id)


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

    def test_create_tool_does_not_store_answer_check_state(self) -> None:
        domain = EducationLiveDomain()
        result = asyncio.run(domain.execute_tool(
            "create_arithmetic_exercise",
            {"operation": "+", "left_operand": 7, "right_operand": 3},
            request=DomainRequest(query="Create an addition exercise"),
            context={},
        ))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.context, {})
        self.assertIsNotNone(result.presentation)

    def test_answer_check_tool_is_not_advertised(self) -> None:
        domain = EducationLiveDomain()
        self.assertEqual(
            [declaration["name"] for declaration in domain.tool_declarations],
            ["create_arithmetic_exercise"],
        )

    def test_education_tool_reaches_live_stage_map_pipeline(self) -> None:
        registry = LiveDomainRegistry()
        registry.register(EducationLiveDomain())
        orchestrator = LiveSessionOrchestrator(
            LiveToolDispatcher(registry),
            presentation_pipeline=PresentationPipeline(template_manager=_EducationTemplateManager()),  # type: ignore[arg-type]
        )
        result = asyncio.run(orchestrator.execute_tool_call_result(
            session_id="education-integration",
            query="Create an addition exercise",
            tool_name="create_arithmetic_exercise",
            arguments={"operation": "+", "left_operand": 3, "right_operand": 2},
        ))
        self.assertEqual(result.response["status"], "completed")
        self.assertNotIn("facts", result.response)
        self.assertIn("visual_stage_map", result.response)
        self.assertIsInstance(result.presentation, RenderedPresentation)


if __name__ == "__main__":
    unittest.main()
