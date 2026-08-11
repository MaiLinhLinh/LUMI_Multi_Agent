"""Grounded facts for the object-group arithmetic template."""

from __future__ import annotations

from typing import Any

from gemini_live.presentation.planner_schemas import GroundedFact


class ObjectGroupMathAdapter:
    """Convert one code-validated exercise into lesson facts and DOM evidence."""

    template_id = "object_group_math"

    def build_candidate_facts(
        self,
        domain_data: dict[str, Any],
        *,
        presentation_capabilities: dict[str, Any],
        presentation_phase: str = "opening",
    ) -> list[GroundedFact]:
        left = self._non_negative_int(domain_data.get("left_count"), "left_count")
        right = self._non_negative_int(domain_data.get("right_count"), "right_count")
        result = self._non_negative_int(domain_data.get("result"), "result")
        operator = domain_data.get("operator")
        asset_label = domain_data.get("asset_label")
        if operator not in {"+", "-"} or not isinstance(asset_label, str) or not asset_label.strip():
            return []
        if (operator == "+" and left + right != result) or (operator == "-" and left - right != result):
            return []

        facts = [
            # self._fact(
            #     "exercise_overview", "arithmetic_exercise", "summary",
            #     {"left_operand": left, "operator": operator, "right_operand": right, "result": result, "asset_label": asset_label},
            #     "overview", "reveal", presentation_capabilities, visualizable=False,
            # ),
            self._fact(
                "left_group", "object_count", "lookup",
                {"count": left, "asset_label": asset_label},
                "group_a", presentation_capabilities,
            ),
            self._fact(
                "operator", "arithmetic_operation", "lookup",
                {"operator": operator},
                "operator", presentation_capabilities, visualizable=False,
            ),
            self._fact(
                "right_group", "object_count", "lookup",
                {"count": right, "asset_label": asset_label},
                "group_b", presentation_capabilities,
            ),
            self._fact(
                "expression", "arithmetic_expression", "summary",
                {"left_operand": left, "operator": operator, "right_operand": right, "result": result},
                "expression", presentation_capabilities,
            ),
            self._fact(
                "result_items", "object_count", "lookup",
                {"count": result, "asset_label": asset_label},
                "result_items", presentation_capabilities,
            ),
            self._fact(
                "answer", "arithmetic_result", "lookup",
                {"result": result, "asset_label": asset_label},
                "answer", presentation_capabilities,
            ),
        ]
        allowed_fact_ids = {
            "opening": {"left_group", "operator", "right_group", "expression"},
            "incorrect_hint": {"left_group", "right_group", "expression"},
            "correct": {"result_items", "answer"},
            "reveal_answer": {"result_items", "answer"}
        }.get(presentation_phase, set())
        return [fact for fact in facts if fact is not None and fact.id in allowed_fact_ids]

    @staticmethod
    def _fact(
        fact_id: str,
        metric: str,
        operation: str,
        value: dict[str, Any],
        focus: str,
        capabilities: dict[str, Any],
        visualizable: bool = True,
    ) -> GroundedFact | None:
        capability = capabilities.get(focus)
        if not isinstance(capability, dict):
            return None
        if not isinstance(capability, dict):
            return None
        return GroundedFact(
            id=fact_id,
            metric=metric,
            operation=operation,  # type: ignore[arg-type]
            value=value,
            focus=focus,
            visualizable=visualizable,
        )

    @staticmethod
    def _non_negative_int(value: Any, _field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("object-group exercise contains an invalid count")
        return value
