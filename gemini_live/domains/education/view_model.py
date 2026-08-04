"""Education data normalisation for trusted Jinja templates."""

from __future__ import annotations

from .models import MathExercise


def object_group_math_view_model(exercise: MathExercise) -> dict[str, object]:
    """Return the exact data contract required by ``object_group_math``."""
    return exercise.to_view_data()

