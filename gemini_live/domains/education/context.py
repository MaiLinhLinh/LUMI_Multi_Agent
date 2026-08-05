"""Server-owned lesson state for interactive Education turns.

The answer remains inside the nested ``lesson_state`` object, which the Live
session does not expose in its compact prompt context.  A later answer-check
tool will read this state rather than asking Gemini to judge arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .models import MathExercise


@dataclass(frozen=True)
class LessonState:
    """One pending or completed interactive math exercise."""

    exercise_id: str
    operation: str
    left_operand: int
    right_operand: int
    correct_answer: int
    asset_id: str
    asset_label: str
    phase: str = "awaiting_answer"
    attempt_count: int = 0

    @classmethod
    def from_exercise(cls, exercise: MathExercise) -> "LessonState":
        return cls(
            exercise_id=str(uuid4()),
            operation=exercise.operation,
            left_operand=exercise.left_operand,
            right_operand=exercise.right_operand,
            correct_answer=exercise.result,
            asset_id=exercise.asset_id,
            asset_label=exercise.asset_label,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "exercise_id": self.exercise_id,
            "operation": self.operation,
            "left_operand": self.left_operand,
            "right_operand": self.right_operand,
            "correct_answer": self.correct_answer,
            "asset_id": self.asset_id,
            "asset_label": self.asset_label,
            "phase": self.phase,
            "attempt_count": self.attempt_count,
        }

    def to_exercise(self) -> MathExercise:
        """Rebuild the trusted exercise required to render the same panel."""
        return MathExercise(
            operation=self.operation,
            left_operand=self.left_operand,
            right_operand=self.right_operand,
            result=self.correct_answer,
            asset_id=self.asset_id,
            asset_label=self.asset_label,
        )

    @classmethod
    def from_context(cls, context: dict[str, Any]) -> "LessonState | None":
        raw = context.get("lesson_state")
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                exercise_id=str(raw["exercise_id"]),
                operation=str(raw["operation"]),
                left_operand=int(raw["left_operand"]),
                right_operand=int(raw["right_operand"]),
                correct_answer=int(raw["correct_answer"]),
                asset_id=str(raw["asset_id"]),
                asset_label=str(raw["asset_label"]),
                phase=str(raw.get("phase", "awaiting_answer")),
                attempt_count=int(raw.get("attempt_count", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return None


class EducationContextResolver:
    """Create and retrieve server-owned state for an Education session."""

    @staticmethod
    def start_exercise(
        exercise: MathExercise,
        view_model: dict[str, object],
    ) -> dict[str, object]:
        state = LessonState.from_exercise(exercise)
        return {
            "lesson_state": state.to_dict(),
            # This scalar is safe for Gemini Live's compact context.  The
            # nested state, including correct_answer, remains server-owned.
            "lesson_phase": state.phase,
            "last_exercise": dict(view_model),
        }

    @staticmethod
    def lesson_state(context: dict[str, Any]) -> LessonState | None:
        return LessonState.from_context(context)

    @staticmethod
    def save_lesson_state(
        context: dict[str, Any],
        state: LessonState,
    ) -> dict[str, object]:
        """Preserve the current exercise panel while replacing its state."""
        updated = dict(context)
        updated["lesson_state"] = state.to_dict()
        updated["lesson_phase"] = state.phase
        return updated
