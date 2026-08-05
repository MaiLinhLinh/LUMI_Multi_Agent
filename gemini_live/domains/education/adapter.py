"""Education router for lesson-representation adapters."""

from __future__ import annotations

from typing import Any

from gemini_live.presentation.base import DomainPresentationAdapter
from gemini_live.presentation.planner_schemas import GroundedFact, PresentationPlan

from .lessons import ObjectGroupMathAdapter
from .prompt import EDUCATION_PRESENTATION_SYSTEM


_INTERACTION_INSTRUCTIONS = {
    "opening": (
        "Introduce the verified exercise, guide observation of the visible "
        "groups and expression, ask one answer question, then stop. Do not "
        "reveal the result."
    ),
    "incorrect_hint": (
        "Give one short visual hint using only the available facts, then ask "
        "the same question again. Do not reveal or imply the result."
    ),
    "correct": (
        "Praise the verified correct answer, then show the verified result "
        "objects and answer. Do not introduce a new exercise."
    ),
    "reveal_answer": (
        "Encourage the child, then show the verified result objects and "
        "answer. Do not introduce a new exercise."
    ),
}


class EducationPresentationAdapter(DomainPresentationAdapter):
    def __init__(self, *, presentation_phase: str = "opening") -> None:
        self._lessons = {ObjectGroupMathAdapter.template_id: ObjectGroupMathAdapter()}
        self._presentation_phase = presentation_phase

    @property
    def domain_id(self) -> str:
        return "education"

    def planner_guidance(self) -> str:
        return EDUCATION_PRESENTATION_SYSTEM

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
        # The current object-group representation has fixed semantic anchors.
        return ObjectGroupMathAdapter.resolve_target(capability)

    def _lesson(self, compact_data: dict[str, Any]) -> ObjectGroupMathAdapter | None:
        template_id = compact_data.get("template_id") if isinstance(compact_data, dict) else None
        return self._lessons.get(template_id) if isinstance(template_id, str) else None

    def _lesson_from_template(
        self, capabilities: dict[str, Any], domain_data: dict[str, Any]
    ) -> ObjectGroupMathAdapter | None:
        # CP-EDU-04 only registers one template. Its overview capability is
        # unique among Education templates at this stage.
        if "overview" in capabilities:
            return self._lessons[ObjectGroupMathAdapter.template_id]
        return None
