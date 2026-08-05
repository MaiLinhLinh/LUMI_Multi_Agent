"""Trusted exercise construction for the Education domain.

Gemini may suggest operands to make lessons varied. This module is the
authority for validation and arithmetic; a lesson/template policy decides
which number range can be visualised.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .models import MathExercise
from .context import LessonState


CREATE_MATH_EXERCISE_DECLARATION: dict[str, Any] = {
    "name": "create_math_exercise",
        "description": (
            "Create one addition or subtraction exercise for a child. "
            "For subtraction, choose the left operand at least as large as the right operand."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operation": {"type": "string", "enum": ["+", "-"]},
            "left_operand": {"type": "integer", "minimum": 0},
            "right_operand": {"type": "integer", "minimum": 0},
        },
        "required": ["operation", "left_operand", "right_operand"],
    },
}

CHECK_CHILD_ANSWER_DECLARATION: dict[str, Any] = {
    "name": "check_child_answer",
    "description": (
        "Check the integer answer that the child just gave for the current math exercise. "
        "Call this instead of deciding whether the child is correct yourself."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
    },
}

ASSETS: tuple[tuple[str, str], ...] = (
    ("flower", "bông hoa"),
    ("ball", "quả bóng"),
    ("rocket", "tên lửa"),
)


class ChoiceSource(Protocol):
    def choice(self, sequence: tuple[tuple[str, str], ...]) -> tuple[str, str]: ...


class ExerciseValidationError(ValueError):
    """Raised when a model-suggested exercise is unsafe or mathematically invalid."""


@dataclass(frozen=True)
class AnswerCheckResult:
    """Verified result of one child answer, never inferred by the LLM."""

    status: str
    state: LessonState


class EducationTools:
    def __init__(self, *, choice_source: ChoiceSource | None = None) -> None:
        self._choice_source = choice_source or random.SystemRandom()

    def create_math_exercise(self, arguments: dict[str, Any]) -> MathExercise:
        operation = str(arguments.get("operation", "")).strip()
        left = self._operand(arguments.get("left_operand"), "left_operand")
        right = self._operand(arguments.get("right_operand"), "right_operand")
        if operation not in {"+", "-"}:
            raise ExerciseValidationError("operation must be '+' or '-'.")

        if operation == "+":
            result = left + right
        else:
            if left < right:
                raise ExerciseValidationError("subtraction cannot produce a negative result.")
            result = left - right

        asset_id, asset_label = self._choice_source.choice(ASSETS)
        return MathExercise(
            operation=operation,
            left_operand=left,
            right_operand=right,
            result=result,
            asset_id=asset_id,
            asset_label=asset_label,
        )

    def check_child_answer(
        self,
        arguments: dict[str, Any],
        state: LessonState,
    ) -> AnswerCheckResult:
        """Compare a Gemini-parsed integer to the server-owned answer.

        The first incorrect answer leads to a hint.  The second incorrect
        answer reveals the verified result, as agreed for the first lesson.
        """
        answer = self._answer(arguments.get("answer"))
        if state.phase != "awaiting_answer":
            return AnswerCheckResult(status="no_pending_exercise", state=state)
        if answer == state.correct_answer:
            return AnswerCheckResult(
                status="correct",
                state=replace(state, phase="completed"),
            )

        next_attempt = state.attempt_count + 1
        if next_attempt >= 2:
            return AnswerCheckResult(
                status="reveal_answer",
                state=replace(state, phase="completed", attempt_count=next_attempt),
            )
        return AnswerCheckResult(
            status="incorrect_hint",
            state=replace(state, attempt_count=next_attempt),
        )

    @staticmethod
    def _operand(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExerciseValidationError(f"{name} must be an integer.")
        if value < 0:
            raise ExerciseValidationError(f"{name} must be non-negative.")
        return value

    @staticmethod
    def _answer(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExerciseValidationError("answer must be an integer.")
        return value
