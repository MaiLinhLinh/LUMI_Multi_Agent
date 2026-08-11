"""Education router for lesson-representation adapters."""

from __future__ import annotations

import re
from typing import Any

from gemini_live.presentation.base import DomainPresentationAdapter
from gemini_live.presentation.planner_schemas import GroundedFact, PresentationPlan

from .lessons import ObjectGroupMathAdapter, RepeatedGroupsArithmeticAdapter
from .prompt import EDUCATION_PRESENTATION_INSTRUCTION, EDUCATION_PRESENTATION_SYSTEM


_INTERACTION_INSTRUCTIONS = {
    "opening": (
        "Introduce the verified exercise, guide observation of the visible "
        "groups and expression, ask one answer question, then stop. Do not "
        "reveal the result."
    ),
    "incorrect_hint": (
        """
        Guide the child to re-observe the available visual facts in sequence.
        For each fact you use, call present_visual before speaking about it.
        Use at least two available visual facts, then repeat the same question.
        Do not reveal or imply the result.
        """
    ),
    "correct": (
        "Praise the verified correct answer, then show the verified result "
        "objects if available and answer. Do not introduce a new exercise."
    ),
    "reveal_answer": (
        "Encourage the child, then show the verified result objects if available and "
        "answer. Do not introduce a new exercise."
    ),
}

_DYNAMIC_TARGET_ID = re.compile(r"^math\.[a-z][a-z0-9._-]*$")


class EducationPresentationAdapter(DomainPresentationAdapter):
    def __init__(self, *, presentation_phase: str = "opening") -> None:
        self._lessons = {
            ObjectGroupMathAdapter.template_id: ObjectGroupMathAdapter(),
            RepeatedGroupsArithmeticAdapter.template_id: RepeatedGroupsArithmeticAdapter(),
        }
        self._presentation_phase = presentation_phase

    @property
    def domain_id(self) -> str:
        return "education"

    def planner_guidance(self) -> str:
        return EDUCATION_PRESENTATION_SYSTEM

    def live_presentation_instruction(self) -> str:
        return EDUCATION_PRESENTATION_INSTRUCTION

    def live_presentation_context(self) -> dict[str, Any]:
        return self.planner_context()

    def live_visual_stage_context(
        self,
        *,
        domain_data: dict[str, Any],
        compact_data: dict[str, Any],
        view_model: dict[str, Any],
    ) -> dict[str, Any]:
        """Expose only the stage state that this lesson phase permits."""

        lesson = self._lesson(compact_data)
        if isinstance(lesson, RepeatedGroupsArithmeticAdapter):
            return lesson.visual_stage_context(
                domain_data,
                presentation_phase=self._presentation_phase,
            )

        result = domain_data.get("result")
        asset_label = domain_data.get("asset_label")
        can_reveal = self._presentation_phase in {"correct", "reveal_answer"}
        if can_reveal and isinstance(result, int) and isinstance(asset_label, str):
            return {
                "result_items_text": f"{result} {asset_label}",
                "result_items_state": "đang ẩn; hiện khi gọi anchor c",
                "result_text": str(result),
                "answer_state": "đang ẩn; hiện khi gọi anchor e",
            }
        return {
            "result_items_text": "?",
            "result_items_state": "đang ẩn",
            "result_text": "?",
            "answer_state": "đang ẩn",
        }

    def planner_context(self) -> dict[str, Any]:
        mode = self._presentation_phase
        return {
            "interaction_mode": mode,
            "interaction_instruction": _INTERACTION_INSTRUCTIONS.get(
                mode,
                _INTERACTION_INSTRUCTIONS["opening"],
            ),
        }

    def build_candidate_facts(
        self,
        domain_data: dict[str, Any],
        *,
        compact_data: dict[str, Any],
        presentation_capabilities: dict[str, Any],
    ) -> list[GroundedFact]:
        lesson = self._lesson(compact_data)
        if lesson is None:
            return []
        return lesson.build_candidate_facts(
            domain_data,
            presentation_capabilities=presentation_capabilities,
            presentation_phase=self._presentation_phase,
        )

    def fallback_plan(
        self,
        domain_data: dict[str, Any],
        capabilities: dict[str, Any],
        grounded_facts: list[GroundedFact],
    ) -> PresentationPlan:
        lesson = self._lesson_from_template(capabilities, domain_data)
        if lesson is None:
            raise ValueError("education template has no registered lesson adapter")
        return lesson.fallback_plan(
            capabilities,
            grounded_facts,
            presentation_phase=self._presentation_phase,
        )

    def resolve_target(
        self,
        capability: dict[str, Any] | None,
        entity: dict[str, Any],
        compact_data: dict[str, Any],
    ) -> str | None:
        target = ObjectGroupMathAdapter.resolve_target(capability)
        if target is not None:
            return target
        if not isinstance(capability, dict):
            return None
        pattern = capability.get("target_pattern")
        group_index = entity.get("group_index") if isinstance(entity, dict) else None
        if not isinstance(pattern, str) or not isinstance(group_index, int) or group_index < 1:
            return None
        target = pattern.replace("{index}", str(group_index))
        if "{" in target or "}" in target:
            return None
        return target if _DYNAMIC_TARGET_ID.fullmatch(target) else None

    def _lesson(
        self, compact_data: dict[str, Any]
    ) -> ObjectGroupMathAdapter | RepeatedGroupsArithmeticAdapter | None:
        template_id = compact_data.get("template_id") if isinstance(compact_data, dict) else None
        return self._lessons.get(template_id) if isinstance(template_id, str) else None

    def _lesson_from_template(
        self, capabilities: dict[str, Any], domain_data: dict[str, Any]
    ) -> ObjectGroupMathAdapter | RepeatedGroupsArithmeticAdapter | None:
        if "group" in capabilities:
            return self._lessons[RepeatedGroupsArithmeticAdapter.template_id]
        if "overview" in capabilities:
            return self._lessons[ObjectGroupMathAdapter.template_id]
        return None
