"""Tests for extracting reusable layout templates from concrete plans."""

from __future__ import annotations

import unittest

from gemini_live_2.catalogs import (
    LayoutTemplate,
    LayoutTemplateError,
    LayoutTemplateMaterializer,
    TemplateExtractor,
)
from gemini_live_2.panel import GridRect, PlanBlock, PresentationPlan
from gemini_live_2.widgets import build_default_widget_registry


class LayoutTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = TemplateExtractor(build_default_widget_registry())
        self.materializer = LayoutTemplateMaterializer()

    def test_extractor_replaces_only_variable_props_with_stable_binding_keys(self) -> None:
        plan = PresentationPlan(
            domain_id="education",
            blocks=(
                PlanBlock("text", GridRect(1, 1, 12, 1), {"content": "Chó và mèo", "role": "title"}),
                PlanBlock("image", GridRect(1, 3, 5, 6), {"asset_id": "dog", "label": "Chó"}),
                PlanBlock("image", GridRect(7, 3, 5, 6), {"asset_id": "cat"}),
            ),
        )

        template = self.extractor.extract(
            plan=plan,
            template_id="two_images_side_by_side",
            description="Hai ảnh lớn cạnh nhau, có tiêu đề.",
        )

        self.assertEqual(template.blocks[0].props, {"content": "$block_1_content", "role": "title"})
        self.assertEqual(template.blocks[1].props, {
            "asset_id": "$block_2_asset_id",
            "label": "$block_2_label",
        })
        self.assertEqual(template.blocks[2].props, {"asset_id": "$block_3_asset_id"})
        self.assertEqual(
            [binding.key for binding in template.bindings],
            ["$block_1_content", "$block_2_asset_id", "$block_2_label", "$block_3_asset_id"],
        )
        self.assertEqual(template.bindings[1].source, "asset_catalog.id")

    def test_layout_template_round_trips_as_json_compatible_contract(self) -> None:
        plan = PresentationPlan(
            domain_id="education",
            blocks=(PlanBlock("image", GridRect(1, 1, 12, 10), {"asset_id": "dog"}),),
        )
        extracted = self.extractor.extract(
            plan=plan,
            template_id="single_image",
            description="Một ảnh lớn.",
        )

        restored = LayoutTemplate.from_dict(extracted.to_dict())

        self.assertEqual(restored, extracted)

    def test_materializer_replaces_all_bindings_before_compilation(self) -> None:
        source = PresentationPlan(
            domain_id="education",
            blocks=(
                PlanBlock("text", GridRect(1, 1, 12, 1), {"content": "Chó và mèo", "role": "title"}),
                PlanBlock("image", GridRect(1, 3, 5, 6), {"asset_id": "dog", "label": "Chó"}),
                PlanBlock("image", GridRect(7, 3, 5, 6), {"asset_id": "cat", "label": "Mèo"}),
            ),
        )
        template = self.extractor.extract(
            plan=source,
            template_id="two_images_side_by_side",
            description="Hai ảnh lớn cạnh nhau.",
        )

        plan = self.materializer.materialize(
            template=template,
            bindings={
                "$block_1_content": "Cùng quan sát hai bạn mèo nhé!",
                "$block_2_asset_id": "cat",
                "$block_2_label": "Mèo 1",
                "$block_3_asset_id": "cat",
                "$block_3_label": "Mèo 2",
            },
        )

        self.assertEqual(plan.template_id, "two_images_side_by_side")
        self.assertEqual(plan.blocks[0].props, {
            "content": "Cùng quan sát hai bạn mèo nhé!",
            "role": "title",
        })
        self.assertEqual(plan.blocks[1].props, {"asset_id": "cat", "label": "Mèo 1"})
        self.assertEqual(plan.blocks[2].props, {"asset_id": "cat", "label": "Mèo 2"})

    def test_materializer_rejects_missing_or_unexpected_bindings(self) -> None:
        source = PresentationPlan(
            domain_id="education",
            blocks=(PlanBlock("image", GridRect(1, 1, 12, 10), {"asset_id": "dog"}),),
        )
        template = self.extractor.extract(
            plan=source,
            template_id="single_image",
            description="Một ảnh lớn.",
        )

        with self.assertRaisesRegex(LayoutTemplateError, "missing bindings"):
            self.materializer.materialize(template=template, bindings={})
        with self.assertRaisesRegex(LayoutTemplateError, "unexpected bindings"):
            self.materializer.materialize(
                template=template,
                bindings={"$block_1_asset_id": "dog", "$extra": "cat"},
            )


if __name__ == "__main__":
    unittest.main()
