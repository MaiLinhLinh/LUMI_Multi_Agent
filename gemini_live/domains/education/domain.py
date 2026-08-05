"""Education domain composition for the shared Gemini Live application."""

from __future__ import annotations

from typing import Any

from gemini_live.domains.base import DomainRequest, DomainToolResult, LiveDomain
from gemini_live.presentation import PresentationRequest

from .adapter import EducationPresentationAdapter
from .context import EducationContextResolver
from .prompt import EDUCATION_LIVE_GUIDANCE
from .tools import (
    CHECK_CHILD_ANSWER_DECLARATION,
    CREATE_MATH_EXERCISE_DECLARATION,
    EducationTools,
    ExerciseValidationError,
)
from .view_model import object_group_math_view_model


class EducationLiveDomain(LiveDomain):
    """Own Education tools and prepare validated lesson data.

    The shared Presentation Pipeline consumes its typed request; it remains
    responsible for template rendering, Planner invocation and compilation.
    """

    def __init__(self) -> None:
        self._tools = EducationTools()
        self._adapter = EducationPresentationAdapter()
        self._context = EducationContextResolver()

    @property
    def domain_id(self) -> str:
        return "education"

    @property
    def tool_declarations(self) -> tuple[dict[str, Any], ...]:
        return (CREATE_MATH_EXERCISE_DECLARATION, CHECK_CHILD_ANSWER_DECLARATION)

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
        if tool_name == "check_child_answer":
            return self._check_child_answer(arguments, context)
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
            context=self._context.start_exercise(exercise, view_model),
            presentation=PresentationRequest(
                domain_id=self.domain_id,
                template_id="object_group_math",
                view_model=view_model,
                adapter=self._adapter,
                domain_data=view_model,
                compact_data=compact_data,
            ),
        )

    def _check_child_answer(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> DomainToolResult:
        state = self._context.lesson_state(context)
        if state is None:
            return DomainToolResult(
                tool_response={
                    "status": "no_pending_exercise",
                    "message": "There is no active math exercise to check.",
                },
                context=dict(context),
            )
        try:
            checked = self._tools.check_child_answer(arguments, state)
        except ExerciseValidationError as exc:
            return DomainToolResult(
                tool_response={"status": "invalid_arguments", "message": str(exc)},
                context=dict(context),
            )

        response: dict[str, Any] = {
            "status": checked.status,
            "attempt_count": checked.state.attempt_count,
            "phase": checked.state.phase,
        }
        if checked.status in {"correct", "reveal_answer"}:
            response["correct_answer"] = checked.state.correct_answer
            response["asset_label"] = checked.state.asset_label
            response["remaining_count"] = checked.state.correct_answer
        presentation = None
        if checked.status in {"incorrect_hint", "correct", "reveal_answer"}:
            view_model = object_group_math_view_model(checked.state.to_exercise())
            presentation = PresentationRequest(
                domain_id=self.domain_id,
                template_id="object_group_math",
                view_model=view_model,
                adapter=EducationPresentationAdapter(presentation_phase=checked.status),
                domain_data=view_model,
                compact_data={"template_id": "object_group_math", **view_model},
            )
        return DomainToolResult(
            tool_response=response,
            context=self._context.save_lesson_state(context, checked.state),
            presentation=presentation,
        )
