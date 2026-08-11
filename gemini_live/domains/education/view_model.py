"""Education data normalisation for trusted Jinja templates."""

from __future__ import annotations

from .models import MathExercise


def object_group_math_view_model(exercise: MathExercise) -> dict[str, object]:
    """Return the exact data contract required by ``object_group_math``."""
    return exercise.to_view_data()


def repeated_groups_arithmetic_view_model(exercise: MathExercise) -> dict[str, object]:
    """Return the trusted equal-group contract for multiplication/division."""

    if exercise.operation == "*":
        group_count = exercise.right_operand
        items_per_group = exercise.left_operand
        operator = "×"
    elif exercise.operation == "/":
        group_count = exercise.right_operand
        items_per_group = exercise.result
        operator = "÷"
    else:
        raise ValueError("repeated-groups view model requires '*' or '/'.")
    return {
        "title": "Cùng học theo nhóm nào!",
        "instruction": "Quan sát các nhóm bằng nhau và tìm kết quả.",
        "asset_id": exercise.asset_id,
        "asset_label": exercise.asset_label,
        "operation": exercise.operation,
        "operator": operator,
        "left_operand": exercise.left_operand,
        "right_operand": exercise.right_operand,
        "result": exercise.result,
        "group_count": group_count,
        "items_per_group": items_per_group,
        "groups": [
            {"item_count": items_per_group}
            for _ in range(group_count)
        ],
    }
