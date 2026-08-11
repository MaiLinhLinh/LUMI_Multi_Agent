"""Render and fact-pack coverage for the equal-groups lesson template."""

from __future__ import annotations

import asyncio
import unittest

from gemini_live.domains import DomainRequest
from gemini_live.domains.education import EducationLiveDomain
from gemini_live.domains.education.models import MathExercise
from gemini_live.domains.education.view_model import repeated_groups_arithmetic_view_model
from gemini_live.presentation.pipeline import PresentationPipeline
from gemini_live.presentation.renderer import JinjaPresentationRenderer


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
                asset_label="bông hoa",
            )
            data = repeated_groups_arithmetic_view_model(exercise)
            panel = renderer.render(
                domain_id="education",
                template_id="repeated_groups_arithmetic",
                data=data,
            )
            self.assertEqual(
                panel.html.count('data-present-id="math.repeated.group.'),
                group_count,
            )
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
                asset_label="bông hoa",
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
            asset_label="tên lửa",
        )
        stage_map = renderer.render_visual_stage_map(
            domain_id="education",
            template_id="repeated_groups_arithmetic",
            data=repeated_groups_arithmetic_view_model(exercise),
            stage_context={"answer_text": "?", "answer_state": "hidden"},
        )
        self.assertIn("ROW 1 (left to right)", stage_map)
        self.assertIn("ROW 2 (left to right)", stage_map)
        self.assertIn("3 rows × 3 columns of tên lửa", stage_map)
        for anchor_id in ("g1", "g2", "g3", "g4", "g5", "d", "e"):
            self.assertIn(f"anchor: {anchor_id}", stage_map)

    def test_multiplication_uses_right_operand_as_group_count(self) -> None:
        domain = EducationLiveDomain()
        result = asyncio.run(domain.execute_tool(
            "create_arithmetic_exercise",
            {"operation": "*", "left_operand": 3, "right_operand": 4},
            request=DomainRequest(query="Ba nhân bốn"),
            context={},
        ))
        assert result.presentation is not None
        self.assertEqual(result.presentation.template_id, "repeated_groups_arithmetic")
        self.assertEqual(result.presentation.view_model["group_count"], 4)
        self.assertEqual(result.presentation.view_model["items_per_group"], 3)

    def test_exact_division_renders_equal_groups(self) -> None:
        domain = EducationLiveDomain()
        result = asyncio.run(domain.execute_tool(
            "create_arithmetic_exercise",
            {"operation": "/", "left_operand": 12, "right_operand": 3},
            request=DomainRequest(query="Mười hai chia ba"),
            context={},
        ))
        assert result.presentation is not None
        self.assertEqual(result.presentation.template_id, "repeated_groups_arithmetic")
        self.assertEqual(result.presentation.view_model["group_count"], 3)
        self.assertEqual(result.presentation.view_model["items_per_group"], 4)
        self.assertEqual(result.presentation.view_model["result"], 4)

        checked = asyncio.run(domain.execute_tool(
            "check_child_answer",
            {"answer": 4},
            request=DomainRequest(query="Bằng bốn"),
            context=result.context,
        ))
        assert checked.presentation is not None
        pipeline = PresentationPipeline()
        prepared = pipeline.prepare(request=checked.presentation)
        fact_pack = pipeline.build_live_fact_pack(checked.presentation, prepared)
        self.assertEqual(fact_pack.anchor_target_map["e"]["target_id"], "math.repeated.answer")

    def test_live_fact_pack_resolves_dynamic_group_anchors(self) -> None:
        domain = EducationLiveDomain()
        result = asyncio.run(domain.execute_tool(
            "create_arithmetic_exercise",
            {"operation": "*", "left_operand": 2, "right_operand": 4},
            request=DomainRequest(query="Hai nhân bốn"),
            context={},
        ))
        assert result.presentation is not None
        pipeline = PresentationPipeline()
        prepared = pipeline.prepare(request=result.presentation)
        pack = pipeline.build_live_fact_pack(result.presentation, prepared)
        self.assertEqual(
            pack.anchor_target_map["g3"]["target_id"],
            "math.repeated.group.3",
        )
        self.assertEqual(pack.anchor_target_map["d"]["target_id"], "math.repeated.expression")
        self.assertNotIn("e", pack.anchor_target_map)


if __name__ == "__main__":
    unittest.main()
