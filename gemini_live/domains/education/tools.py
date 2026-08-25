"""Trusted arithmetic-exercise construction for the Education domain.

Gemini may suggest operands to make lessons varied. This module is the
authority for validation and arithmetic; a lesson/template policy decides
which number range can be visualised.
"""

from __future__ import annotations

import random
from typing import Any, Protocol

from .models import MathExercise


CREATE_ARITHMETIC_EXERCISE_DECLARATION: dict[str, Any] = {
    "name": "create_arithmetic_exercise",
    "description": (
        "Create one arithmetic exercise for a child from proposed integer operands. "
        "Supported operations are addition (+), subtraction (-), multiplication (*), "
        "and exact division (/). For subtraction, left_operand must be at least "
        "right_operand. For division, right_operand must be non-zero and left_operand "
        "must be exactly divisible by right_operand."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operation": {"type": "string", "enum": ["+", "-", "*", "/"]},
            "left_operand": {"type": "integer", "minimum": 0},
            "right_operand": {"type": "integer", "minimum": 0},
        },
        "required": ["operation", "left_operand", "right_operand"],
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


class EducationTools:
    def __init__(self, *, choice_source: ChoiceSource | None = None) -> None:
        self._choice_source = choice_source or random.SystemRandom()

    def create_arithmetic_exercise(self, arguments: dict[str, Any]) -> MathExercise:
        operation = str(arguments.get("operation", "")).strip()
        left = self._operand(arguments.get("left_operand"), "left_operand")
        right = self._operand(arguments.get("right_operand"), "right_operand")
        if operation not in {"+", "-", "*", "/"}:
            raise ExerciseValidationError("operation must be '+', '-', '*', or '/'.")

        if operation == "+":
            result = left + right
        elif operation == "-":
            if left < right:
                raise ExerciseValidationError("subtraction cannot produce a negative result.")
            result = left - right
        elif operation == "*":
            result = left * right
        else:
            if right == 0:
                raise ExerciseValidationError("division cannot use zero as the right operand.")
            if left % right != 0:
                raise ExerciseValidationError("division must produce an integer result.")
            result = left // right

        asset_id, asset_label = self._choice_source.choice(ASSETS)
        return MathExercise(
            operation=operation,
            left_operand=left,
            right_operand=right,
            result=result,
            asset_id=asset_id,
            asset_label=asset_label,
        )

    @staticmethod
    def _operand(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExerciseValidationError(f"{name} must be an integer.")
        if value < 0:
            raise ExerciseValidationError(f"{name} must be non-negative.")
        return value
