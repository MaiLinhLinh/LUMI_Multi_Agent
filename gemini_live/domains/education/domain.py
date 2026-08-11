"""Education domain composition for the shared Gemini Live application."""

from __future__ import annotations

from typing import Any

from gemini_live.domains.base import DomainRequest, DomainResult, LiveDomain
from gemini_live.presentation import PresentationRequest

from .adapter import EducationPresentationAdapter
from .context import EducationContextResolver
from .prompt import EDUCATION_LIVE_GUIDANCE
from .tools import (
    CHECK_CHILD_ANSWER_DECLARATION,
    CREATE_ARITHMETIC_EXERCISE_DECLARATION,
    EducationTools,
    ExerciseValidationError,
)
from .view_model import object_group_math_view_model, repeated_groups_arithmetic_view_model


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
        return (CREATE_ARITHMETIC_EXERCISE_DECLARATION, CHECK_CHILD_ANSWER_DECLARATION)

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
    ) -> DomainResult:
        if tool_name == "check_child_answer":
            return self._execute_check_child_answer(arguments, context)
        if tool_name != "create_arithmetic_exercise":
            raise ValueError(f"Education does not own tool {tool_name!r}.")
        return self._execute_create_arithmetic_exercise(arguments, context)

    def _execute_create_arithmetic_exercise(
        self,
        arguments: dict[str, Any],
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

        template_id, view_model = self._view_model_for_exercise(exercise)
        compact_data = {"template_id": template_id, **view_model}
        return DomainResult(
            status="completed",
            context=self._context.start_exercise(exercise, view_model),
            presentation=PresentationRequest(
                domain_id=self.domain_id,
                template_id=template_id,
                view_model=view_model,
                adapter=self._adapter,
                domain_data=view_model,
                compact_data=compact_data,
            ),
        )

    def _execute_check_child_answer(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> DomainResult:
        state = self._context.lesson_state(context)
        if state is None:
            return DomainResult(
                status="no_pending_exercise",
                context=dict(context),
                detail="There is no active math exercise to check.",
            )
        try:
            checked = self._tools.check_child_answer(arguments, state)
        except ExerciseValidationError as exc:
            return DomainResult(
                status="invalid_arguments",
                context=dict(context),
                detail=str(exc),
            )

        presentation = None
        if checked.status in {"incorrect_hint", "correct", "reveal_answer"}:
            template_id, view_model = self._view_model_for_exercise(checked.state.to_exercise())
            presentation = PresentationRequest(
                domain_id=self.domain_id,
                template_id=template_id,
                view_model=view_model,
                adapter=EducationPresentationAdapter(presentation_phase=checked.status),
                domain_data=view_model,
                compact_data={"template_id": template_id, **view_model},
                render_panel=False,
            )
        return DomainResult(
            status=checked.status,
            context=self._context.save_lesson_state(context, checked.state),
            presentation=presentation,
        )

    @staticmethod
    def _view_model_for_exercise(exercise: Any) -> tuple[str, dict[str, object]]:
        if exercise.operation in {"+", "-"}:
            return "object_group_math", object_group_math_view_model(exercise)
        if exercise.operation in {"*", "/"}:
            return "repeated_groups_arithmetic", repeated_groups_arithmetic_view_model(exercise)
        raise ValueError(f"unsupported arithmetic operation: {exercise.operation!r}")
