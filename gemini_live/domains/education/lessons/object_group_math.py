"""Grounded facts for the object-group arithmetic template."""

from __future__ import annotations

import re
from typing import Any

from gemini_live.presentation.planner_runtime import fallback_presentation_plan
from gemini_live.presentation.planner_schemas import GroundedFact, PresentationPlan, PresentationStep


_TARGET_ID = re.compile(r"^math\.[a-z][a-z0-9._-]*$")


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
                "group_a", "draw_circle", presentation_capabilities, anchor_id="a",
            ),
            self._fact(
                "operator", "arithmetic_operation", "lookup",
                {"operator": operator},
                "operator", "highlight", presentation_capabilities, visualizable=False,
            ),
            self._fact(
                "right_group", "object_count", "lookup",
                {"count": right, "asset_label": asset_label},
                "group_b", "draw_circle", presentation_capabilities, anchor_id="b",
            ),
            self._fact(
                "expression", "arithmetic_expression", "summary",
                {"left_operand": left, "operator": operator, "right_operand": right, "result": result},
                "expression", "highlight", presentation_capabilities, anchor_id="d",
            ),
            self._fact(
                "result_items", "object_count", "lookup",
                {"count": result, "asset_label": asset_label},
                "result_items", "reveal_items", presentation_capabilities, anchor_id="c",
            ),
            self._fact(
                "answer", "arithmetic_result", "lookup",
                {"result": result, "asset_label": asset_label},
                "answer", "reveal", presentation_capabilities, anchor_id="e",
            ),
        ]
        allowed_fact_ids = {
            "opening": {"left_group", "operator", "right_group", "expression"},
            "incorrect_hint": {"left_group", "right_group", "expression"},
            "correct": {"result_items", "answer"},
            "reveal_answer": {"result_items", "answer"}
        }.get(presentation_phase, set())
        return [fact for fact in facts if fact is not None and fact.id in allowed_fact_ids]

    def fallback_plan(
        self,
        capabilities: dict[str, Any],
        grounded_facts: list[GroundedFact],
        *,
        presentation_phase: str = "opening",
    ) -> PresentationPlan:
        fact_by_id = {fact.id: fact for fact in grounded_facts}
        if presentation_phase in {"correct", "reveal_answer"}:
            result_items = fact_by_id.get("result_items")
            answer = fact_by_id.get("answer")
            if result_items is not None and answer is not None:
                answer_value = answer.value if isinstance(answer.value, dict) else {}
                result = answer_value.get("result")
                asset_label = answer_value.get("asset_label")
                opening = "Chính xác rồi!" if presentation_phase == "correct" else "Không sao đâu, mình cùng xem đáp án nhé."
                return PresentationPlan(steps=[
                    PresentationStep(
                        narration=f"{opening} Còn lại {result} {asset_label}.",
                        fact_id=result_items.id,
                        effect=result_items.effect_hint,
                        gesture="explain",
                    ),
                    PresentationStep(
                        narration=f"Đáp án là {result}.",
                        fact_id=answer.id,
                        effect=answer.effect_hint,
                        gesture="explain",
                    ),
                ])
        if presentation_phase == "incorrect_hint":
            left = fact_by_id.get("left_group")
            right = fact_by_id.get("right_group")
            expression = fact_by_id.get("expression")
            if left is not None and right is not None and expression is not None:
                value = expression.value if isinstance(expression.value, dict) else {}
                return PresentationPlan(steps=[
                    PresentationStep(
                        narration="Con nhìn lại nhóm này nhé.",
                        fact_id=left.id,
                        effect=left.effect_hint,
                        gesture="point_left",
                    ),
                    PresentationStep(
                        narration="Mình bớt đi nhóm này.",
                        fact_id=right.id,
                        effect=right.effect_hint,
                        gesture="point_right",
                    ),
                    PresentationStep(
                        f"{value.get('left_operand')} {value.get('operator')} {value.get('right_operand')} bằng bao nhiêu nhỉ?",
                        fact_id=expression.id,
                        effect=expression.effect_hint,
                        gesture="explain",
                    ),
                ])
        expression = next((fact for fact in grounded_facts if fact.id == "expression"), None)
        if expression is None:
            return fallback_presentation_plan(
                capabilities=capabilities,
                grounded_facts=grounded_facts,
                fallback_narration="Cùng quan sát bài toán trên màn hình nhé.",
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
    def resolve_target(capability: dict[str, Any] | None) -> str | None:
        target = capability.get("target_id") if isinstance(capability, dict) else None
        return target if isinstance(target, str) and _TARGET_ID.fullmatch(target) else None

    @staticmethod
    def _fact(
        fact_id: str,
        metric: str,
        operation: str,
        value: dict[str, Any],
        focus: str,
        effect_hint: str,
        capabilities: dict[str, Any],
        *,
        anchor_id: str | None = None,
        visualizable: bool = True,
    ) -> GroundedFact | None:
        capability = capabilities.get(focus)
        if not isinstance(capability, dict):
            return None
        allowed = capability.get("allowed_effects")
        if not isinstance(allowed, list) or effect_hint not in allowed:
            return None
        target = ObjectGroupMathAdapter.resolve_target(capability)
        if target is None:
            return None
        return GroundedFact(
            id=fact_id,
            metric=metric,
            operation=operation,  # type: ignore[arg-type]
            value=value,
            focus=focus,
            effect_hint=effect_hint,  # type: ignore[arg-type]
            anchor_id=anchor_id,
            visualizable=visualizable,
            visual_evidence={"kind": "static_target", "target_id": target},
        )

    @staticmethod
    def _non_negative_int(value: Any, _field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("object-group exercise contains an invalid count")
        return value
