"""Trusted stage state for multiplication and exact-division templates."""

from __future__ import annotations

from typing import Any


class RepeatedGroupsArithmeticAdapter:
    """Supply only the answer visibility state needed by the ASCII map."""

    template_id = "repeated_groups_arithmetic"

    def visual_stage_context(
        self,
        domain_data: dict[str, Any],
        *,
        presentation_phase: str,
    ) -> dict[str, Any]:
        result = domain_data.get("result")
        reveal_answer = presentation_phase in {"correct", "reveal_answer"}
        return {
            "answer_text": str(result) if reveal_answer and isinstance(result, int) else "?",
            "answer_state": (
                "đang hiện; được phép công bố"
                if reveal_answer
                else "đang ẩn; chưa được phép công bố"
            ),
        }
