"""Grounded facts for multiplication and exact-division equal-group lessons."""

from __future__ import annotations

import re
from typing import Any

from gemini_live.presentation.planner_runtime import fallback_presentation_plan
from gemini_live.presentation.planner_schemas import GroundedFact, PresentationPlan, PresentationStep


_TARGET_ID = re.compile(r"^math\.[a-z][a-z0-9._-]*$")


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
                effect_hint="draw_circle",
                capabilities=presentation_capabilities,
                entity={"group_index": index},
                anchor_id=f"g{index}",
                target_id=f"math.repeated.group.{index}",
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
            effect_hint="highlight",
            capabilities=presentation_capabilities,
            anchor_id="d",
            target_id="math.repeated.expression",
        )
        answer = self._fact(
            fact_id="answer",
            metric="arithmetic_result",
            operation="lookup",
            value={"result": result, "asset_label": asset_label},
            focus="answer",
            effect_hint="reveal",
            capabilities=presentation_capabilities,
            anchor_id="e",
            target_id="math.repeated.answer",
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

    def fallback_plan(
        self,
        capabilities: dict[str, Any],
        grounded_facts: list[GroundedFact],
        *,
        presentation_phase: str = "opening",
    ) -> PresentationPlan:
        fact_by_id = {fact.id: fact for fact in grounded_facts}
        if presentation_phase in {"correct", "reveal_answer"}:
            answer = fact_by_id.get("answer")
            if answer is not None:
                value = answer.value if isinstance(answer.value, dict) else {}
                return PresentationPlan(steps=[PresentationStep(
                    narration=f"Đáp án là {value.get('result')}.",
                    fact_id=answer.id,
                    effect=answer.effect_hint,
                    gesture="explain",
                )])
        expression = fact_by_id.get("expression")
        if expression is None:
            return fallback_presentation_plan(
                capabilities=capabilities,
                grounded_facts=grounded_facts,
                fallback_narration="Cùng quan sát các nhóm trên màn hình nhé.",
            )
        value = expression.value if isinstance(expression.value, dict) else {}
        return PresentationPlan(steps=[PresentationStep(
            narration=(
                f"{value.get('left_operand')} {value.get('operator')} "
                f"{value.get('right_operand')} bằng bao nhiêu nhỉ?"
            ),
            fact_id=expression.id,
            effect=expression.effect_hint,
            gesture="explain",
        )])

    @staticmethod
    def _fact(
        *,
        fact_id: str,
        metric: str,
        operation: str,
        value: dict[str, Any],
        focus: str,
        effect_hint: str,
        capabilities: dict[str, Any],
        entity: dict[str, Any] | None = None,
        anchor_id: str,
        target_id: str,
    ) -> GroundedFact | None:
        capability = capabilities.get(focus)
        allowed = capability.get("allowed_effects") if isinstance(capability, dict) else None
        if not isinstance(allowed, list) or effect_hint not in allowed:
            return None
        if not _TARGET_ID.fullmatch(target_id):
            return None
        return GroundedFact(
            id=fact_id,
            metric=metric,
            operation=operation,  # type: ignore[arg-type]
            value=value,
            focus=focus,
            effect_hint=effect_hint,  # type: ignore[arg-type]
            entity=entity or {},
            anchor_id=anchor_id,
            visualizable=True,
            visual_evidence={"kind": "static_target", "target_id": target_id},
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
