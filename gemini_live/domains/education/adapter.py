"""Education prompt selection and trusted stage state."""

from __future__ import annotations

from typing import Any

from gemini_live.presentation.base import DomainPresentationAdapter

from .lessons import RepeatedGroupsArithmeticAdapter
from .prompt import EDUCATION_PRESENTATION_INSTRUCTION, EDUCATION_STAGE_GOALS


class EducationPresentationAdapter(DomainPresentationAdapter):
    def __init__(self, *, presentation_phase: str = "opening") -> None:
        self._repeated_groups_lesson = RepeatedGroupsArithmeticAdapter()
        self._presentation_phase = presentation_phase

    @property
    def domain_id(self) -> str:
        return "education"

    def live_presentation_instruction(self) -> str:
        return EDUCATION_PRESENTATION_INSTRUCTION

    def live_visual_stage_context(
        self,
        *,
        domain_data: dict[str, Any],
        compact_data: dict[str, Any],
        view_model: dict[str, Any],
    ) -> dict[str, Any]:
        """Expose answer state and the current teaching goal inside the stage map."""

        if compact_data.get("template_id") == self._repeated_groups_lesson.template_id:
            stage_context = self._repeated_groups_lesson.visual_stage_context(
                domain_data,
                presentation_phase=self._presentation_phase,
            )
        else:
            result = domain_data.get("result")
            asset_label = domain_data.get("asset_label")
            can_reveal = self._presentation_phase in {"correct", "reveal_answer"}
            if can_reveal and isinstance(result, int) and isinstance(asset_label, str):
                stage_context = {
                    "result_items_text": f"{result} {asset_label}",
                    "result_items_state": "đang ẩn; được phép công bố khi gọi anchor c",
                    "result_text": str(result),
                    "answer_state": "đang ẩn; được phép công bố khi gọi anchor e",
                }
            else:
                stage_context = {
                    "result_items_text": "?",
                    "result_items_state": "đang ẩn; chưa được phép công bố",
                    "result_text": "?",
                    "answer_state": "đang ẩn; chưa được phép công bố",
                }

        stage_context["presentation_goal"] = EDUCATION_STAGE_GOALS.get(
            self._presentation_phase,
            EDUCATION_STAGE_GOALS["opening"],
        )
        return stage_context
