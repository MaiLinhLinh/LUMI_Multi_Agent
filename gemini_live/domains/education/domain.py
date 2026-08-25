"""Education domain composition for the shared Gemini Live application."""

from __future__ import annotations

from typing import Any

from gemini_live.domains.base import DomainRequest, DomainResult, LiveDomain
from gemini_live.presentation import PresentationRequest

from .prompt import EDUCATION_LIVE_GUIDANCE, EDUCATION_PRESENTATION_INSTRUCTION
from .tools import (
    CREATE_ARITHMETIC_EXERCISE_DECLARATION,
    EducationTools,
    ExerciseValidationError,
)
from .view_model import object_group_math_view_model, repeated_groups_arithmetic_view_model


class EducationLiveDomain(LiveDomain):
    """Own Education tools and prepare validated lesson data."""

    def __init__(self) -> None:
        self._tools = EducationTools()

    @property
    def domain_id(self) -> str:
        return "education"

    @property
    def tool_declarations(self) -> tuple[dict[str, Any], ...]:
        return (CREATE_ARITHMETIC_EXERCISE_DECLARATION,)

    @property
    def prompt_guidance(self) -> str:
        return EDUCATION_LIVE_GUIDANCE

    @property
    def presentation_instruction(self) -> str:
        return EDUCATION_PRESENTATION_INSTRUCTION

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        request: DomainRequest,
        context: dict[str, Any],
    ) -> DomainResult:
        if tool_name != "create_arithmetic_exercise":
            raise ValueError(f"Education does not own tool {tool_name!r}.")
        return self._execute_create_arithmetic_exercise(arguments, request, context)

    def _execute_create_arithmetic_exercise(
        self,
        arguments: dict[str, Any],
        request: DomainRequest,
        context: dict[str, Any],
    ) -> DomainResult:
        try:
            exercise = self._tools.create_arithmetic_exercise(arguments)
        except ExerciseValidationError as exc:
            return DomainResult(
                status="invalid_arguments",
                context=dict(context),
                detail=str(exc),
            )

        view_model = self._render_data_for_exercise(exercise)
        return DomainResult(
            status="completed",
            context=dict(context),
            presentation=PresentationRequest(
                domain_id=self.domain_id,
                presentation_brief=request.query,
                render_data=view_model,
                presentation_instruction=EDUCATION_PRESENTATION_INSTRUCTION,
            ),
        )

    @staticmethod
    def _render_data_for_exercise(exercise: Any) -> dict[str, object]:
        if exercise.operation in {"+", "-"}:
            return object_group_math_view_model(exercise)
        if exercise.operation in {"*", "/"}:
            return repeated_groups_arithmetic_view_model(exercise)
        raise ValueError(f"unsupported arithmetic operation: {exercise.operation!r}")
