"""Validated, server-owned data models for the first Education exercise."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MathExercise:
    """One addition or subtraction exercise whose answer is computed by code."""

    operation: str
    left_operand: int
    right_operand: int
    result: int
    asset_id: str
    asset_label: str

    def to_view_data(self) -> dict[str, object]:
        return {
            "title": "Cùng tính nào!",
            "instruction": "Quan sát các nhóm hình và tìm kết quả.",
            "asset_id": self.asset_id,
            "asset_label": self.asset_label,
            "left_count": self.left_operand,
            "right_count": self.right_operand,
            "operator": self.operation,
            "result": self.result,
        }

