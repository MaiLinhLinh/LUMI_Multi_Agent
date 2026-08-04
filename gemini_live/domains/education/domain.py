"""Education domain composition for the shared Gemini Live application."""

from __future__ import annotations

from typing import Any

from gemini_live.domains.base import DomainRequest, DomainToolResult, LiveDomain
from gemini_live.presentation import PresentationRequest

from .adapter import EducationPresentationAdapter
from .prompt import EDUCATION_LIVE_GUIDANCE
from .tools import CREATE_MATH_EXERCISE_DECLARATION, EducationTools, ExerciseValidationError
from .view_model import object_group_math_view_model


class EducationLiveDomain(LiveDomain):
    """Own Education tools and prepare validated lesson data.

    The shared Presentation Pipeline consumes its typed request; it remains
    responsible for template rendering, Planner invocation and compilation.
    """

    def __init__(self) -> None:
        self._tools = EducationTools()
        self._adapter = EducationPresentationAdapter()

    @property
    def domain_id(self) -> str:
        return "education"

    @property
    def tool_declarations(self) -> tuple[dict[str, Any], ...]:
        return (CREATE_MATH_EXERCISE_DECLARATION,)

    @property
    def prompt_guidance(self) -> str:
        return EDUCATION_LIVE_GUIDANCE

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        request: DomainRequest,
        context: dict[str, Any],
    ) -> DomainToolResult:
        if tool_name != "create_math_exercise":
            raise ValueError(f"Education does not own tool {tool_name!r}.")
        try:
            exercise = self._tools.create_math_exercise(arguments)
        except ExerciseValidationError as exc:
            return DomainToolResult(
                tool_response={
                    "status": "invalid_arguments",
                    "message": str(exc),
                },
                context=dict(context),
            )

        view_model = object_group_math_view_model(exercise)
        compact_data = {"template_id": "object_group_math", **view_model}
        return DomainToolResult(
            tool_response={
                "status": "completed",
                "domain_id": self.domain_id,
                "exercise": {
                    "operation": exercise.operation,
                    "left_operand": exercise.left_operand,
                    "right_operand": exercise.right_operand,
                    "result": exercise.result,
                    "asset_label": exercise.asset_label,
                },
            },
            context={"last_exercise": view_model},
            presentation=PresentationRequest(
                domain_id=self.domain_id,
                template_id="object_group_math",
                view_model=view_model,
                adapter=self._adapter,
                domain_data=view_model,
                compact_data=compact_data,
            ),
        )
