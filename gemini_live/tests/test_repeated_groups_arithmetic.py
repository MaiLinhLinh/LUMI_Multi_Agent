"""Render coverage for the equal-groups lesson template."""

from __future__ import annotations

import asyncio
import unittest

from gemini_live.domains import DomainRequest
from gemini_live.domains.education import EducationLiveDomain
from gemini_live.domains.education.models import MathExercise
from gemini_live.domains.education.view_model import repeated_groups_arithmetic_view_model
from gemini_live.presentation.pipeline import PresentationPipeline
from gemini_live.presentation.renderer import JinjaPresentationRenderer
from gemini_live.template_engine.template_manager import TemplateResolution


class _RepeatedGroupsTemplateManager:
    async def resolve(
        self,
        request: object,
        *,
        recent_history: tuple[dict[str, str], ...] = (),
    ) -> TemplateResolution:
        del request, recent_history
        return TemplateResolution(decision="use_existing", template_id="repeated_groups_arithmetic")


class RepeatedGroupsArithmeticTests(unittest.TestCase):
    def test_template_renders_three_four_and_eight_groups(self) -> None:
        renderer = JinjaPresentationRenderer()
        for group_count in (3, 4, 8):
            exercise = MathExercise(
                operation="*",
                left_operand=2,
                right_operand=group_count,
                result=2 * group_count,
                asset_id="flower",
                asset_label="flower",
            )
            panel = renderer.render(
                domain_id="education",
                template_id="repeated_groups_arithmetic",
                data=repeated_groups_arithmetic_view_model(exercise),
            )
            self.assertEqual(panel.html.count('data-present-id="math.repeated.group.'), group_count)
            self.assertIn('data-present-id="math.repeated.answer"', panel.html)

    def test_template_uses_near_square_item_grid(self) -> None:
        renderer = JinjaPresentationRenderer()
        for item_count, expected_columns in ((4, 2), (6, 3), (9, 3)):
            exercise = MathExercise(
                operation="*",
                left_operand=item_count,
                right_operand=3,
                result=item_count * 3,
                asset_id="flower",
                asset_label="flower",
            )
            panel = renderer.render(
                domain_id="education",
                template_id="repeated_groups_arithmetic",
                data=repeated_groups_arithmetic_view_model(exercise),
            )
            self.assertIn(f"--item-columns: {expected_columns};", panel.html)

    def test_visual_stage_map_uses_same_group_rows_and_dynamic_anchors(self) -> None:
        renderer = JinjaPresentationRenderer()
        exercise = MathExercise(
            operation="*",
            left_operand=9,
            right_operand=5,
            result=45,
            asset_id="rocket",
            asset_label="rocket",
        )
        stage_map = renderer.render_visual_stage_map(
            domain_id="education",
            template_id="repeated_groups_arithmetic",
            data=repeated_groups_arithmetic_view_model(exercise),
            stage_context={"answer_text": "?", "answer_state": "hidden"},
        )
        self.assertIn("ROW 1 (left to right)", stage_map)
        self.assertIn("ROW 2 (left to right)", stage_map)
        self.assertIn("3 rows", stage_map)
        for anchor_id in ("g1", "g2", "g3", "g4", "g5", "d", "e"):
            self.assertIn(f"anchor: {anchor_id}", stage_map)

    def test_multiplication_uses_right_operand_as_group_count(self) -> None:
        domain = EducationLiveDomain()
        result = asyncio.run(domain.execute_tool(
            "create_arithmetic_exercise",
            {"operation": "*", "left_operand": 3, "right_operand": 4},
            request=DomainRequest(query="Three times four"),
            context={},
        ))
        assert result.presentation is not None
        self.assertIsNone(result.presentation.template_id)
        self.assertEqual(result.presentation.render_data["group_count"], 4)
        self.assertEqual(result.presentation.render_data["items_per_group"], 3)

    def test_exact_division_exposes_the_answer_anchor_without_answer_check_tool(self) -> None:
        domain = EducationLiveDomain()
        result = asyncio.run(domain.execute_tool(
            "create_arithmetic_exercise",
            {"operation": "/", "left_operand": 12, "right_operand": 3},
            request=DomainRequest(query="Twelve divided by three"),
            context={},
        ))
        assert result.presentation is not None
        self.assertIsNone(result.presentation.template_id)
        self.assertEqual(result.presentation.render_data["group_count"], 3)
        self.assertEqual(result.presentation.render_data["items_per_group"], 4)
        self.assertEqual(result.presentation.render_data["result"], 4)

        pipeline = PresentationPipeline(template_manager=_RepeatedGroupsTemplateManager())  # type: ignore[arg-type]
        resolved = asyncio.run(pipeline.resolve_template(request=result.presentation))
        prepared = pipeline.prepare(request=resolved)
        presentation_pack = pipeline.build_live_presentation_pack(prepared)
        self.assertEqual(presentation_pack.panel_anchor_map["e"]["target_id"], "math.repeated.answer")

    def test_presentation_pack_resolves_dynamic_group_anchors(self) -> None:
        domain = EducationLiveDomain()
        result = asyncio.run(domain.execute_tool(
            "create_arithmetic_exercise",
            {"operation": "*", "left_operand": 2, "right_operand": 4},
            request=DomainRequest(query="Two times four"),
            context={},
        ))
        assert result.presentation is not None
        pipeline = PresentationPipeline(template_manager=_RepeatedGroupsTemplateManager())  # type: ignore[arg-type]
        resolved = asyncio.run(pipeline.resolve_template(request=result.presentation))
        prepared = pipeline.prepare(request=resolved)
        pack = pipeline.build_live_presentation_pack(prepared)
        self.assertEqual(pack.panel_anchor_map["g3"]["target_id"], "math.repeated.group.3")
        self.assertEqual(pack.panel_anchor_map["d"]["target_id"], "math.repeated.expression")
        self.assertEqual(pack.panel_anchor_map["e"]["target_id"], "math.repeated.answer")


if __name__ == "__main__":
    unittest.main()
