"""Grounded facts for multiplication and exact-division equal-group lessons."""

from __future__ import annotations

from typing import Any

from gemini_live.presentation.planner_schemas import GroundedFact


class RepeatedGroupsArithmeticAdapter:
    """Represent verified multiplication or division as equal object groups.

    ``a × b`` becomes ``b`` groups with ``a`` objects each.  ``a ÷ b``
    becomes ``b`` groups with ``a / b`` objects each.
    """

    template_id = "repeated_groups_arithmetic"

    def build_candidate_facts(
        self,
        domain_data: dict[str, Any],
        *,
        presentation_capabilities: dict[str, Any],
        presentation_phase: str = "opening",
    ) -> list[GroundedFact]:
        operator = domain_data.get("operator")
        asset_label = domain_data.get("asset_label")
        group_count = self._non_negative_int(domain_data.get("group_count"), "group_count")
        items_per_group = self._non_negative_int(
            domain_data.get("items_per_group"), "items_per_group"
        )
        result = self._non_negative_int(domain_data.get("result"), "result")
        left_operand = self._non_negative_int(
            domain_data.get("left_operand"), "left_operand"
        )
        groups = domain_data.get("groups")
        if operator not in {"×", "÷"} or not isinstance(asset_label, str) or not asset_label.strip():
            return []
        if not isinstance(groups, list) or len(groups) != group_count:
            return []
        represented_total = group_count * items_per_group
        if group_count < 1:
            return []
        if operator == "×" and represented_total != result:
            return []
        if operator == "÷" and represented_total != left_operand:
            return []

        facts: list[GroundedFact] = []
        for index, group in enumerate(groups, start=1):
            item_count = self._group_item_count(group, items_per_group)
            if item_count != items_per_group:
                return []
            fact = self._fact(
                fact_id=f"group_{index}",
                metric="equal_group_count",
                operation="lookup",
                value={
                    "group_number": index,
                    "count": item_count,
                    "asset_label": asset_label,
                },
                focus="group",
                capabilities=presentation_capabilities,
                entity={"group_index": index},
            )
            if fact is not None:
                facts.append(fact)

        expression = self._fact(
            fact_id="expression",
            metric="arithmetic_expression",
            operation="summary",
            value={
                "left_operand": domain_data.get("left_operand"),
                "operator": operator,
                "right_operand": domain_data.get("right_operand"),
            },
            focus="expression",
            capabilities=presentation_capabilities,
        )
        answer = self._fact(
            fact_id="answer",
            metric="arithmetic_result",
            operation="lookup",
            value={"result": result, "asset_label": asset_label},
            focus="answer",
            capabilities=presentation_capabilities,
        )
        if expression is not None:
            facts.append(expression)
        if answer is not None and presentation_phase in {"correct", "reveal_answer"}:
            facts.append(answer)
        return facts

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
            "answer_state": "visible" if reveal_answer else "hidden",
        }

    @staticmethod
    def _fact(
        *,
        fact_id: str,
        metric: str,
        operation: str,
        value: dict[str, Any],
        focus: str,
        capabilities: dict[str, Any],
        entity: dict[str, Any] | None = None,
    ) -> GroundedFact | None:
        capability = capabilities.get(focus)
        if not isinstance(capability, dict):
            return None
        return GroundedFact(
            id=fact_id,
            metric=metric,
            operation=operation,  # type: ignore[arg-type]
            value=value,
            focus=focus,
            entity=entity or {},
            visualizable=True,
        )

    @staticmethod
    def _group_item_count(group: Any, default: int) -> int:
        if not isinstance(group, dict):
            raise ValueError("repeated-groups exercise contains an invalid group")
        return RepeatedGroupsArithmeticAdapter._non_negative_int(
            group.get("item_count", default), "item_count"
        )

    @staticmethod
    def _non_negative_int(value: Any, _field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("repeated-groups exercise contains an invalid count")
        return value
