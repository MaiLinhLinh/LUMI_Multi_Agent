"""Tests for the untrusted Template LLM Layout Spec contract."""

from __future__ import annotations

import unittest

from gemini_live.template_engine.layout_contract import (
    CANVAS_COLUMNS,
    CANVAS_ROWS,
    LayoutSpecValidationError,
    validate_template_layout_output,
)


class TemplateLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assets = {"dog", "cat"}

    def test_accepts_the_dog_cat_layout(self) -> None:
        spec = validate_template_layout_output(_dog_cat_layout(), allowed_asset_ids=self.assets)

        self.assertEqual((spec.columns, spec.rows), (CANVAS_COLUMNS, CANVAS_ROWS))
        self.assertEqual([block.id for block in spec.blocks], ["b1", "b2", "b3", "b4"])

    def test_rejects_uncatalogued_asset(self) -> None:
        payload = _dog_cat_layout()
        payload["blocks"][2]["asset_id"] = "hamster"

        with self.assertRaisesRegex(LayoutSpecValidationError, "not catalogued"):
            validate_template_layout_output(payload, allowed_asset_ids=self.assets)

    def test_rejects_out_of_bounds_grid_placement(self) -> None:
        payload = _dog_cat_layout()
        payload["blocks"][3]["grid"] = {"col": 10, "row": 3, "col_span": 4, "row_span": 5}

        with self.assertRaisesRegex(LayoutSpecValidationError, "exceeds"):
            validate_template_layout_output(payload, allowed_asset_ids=self.assets)

    def test_rejects_overlapping_content_blocks(self) -> None:
        payload = _dog_cat_layout()
        payload["blocks"][3]["grid"] = {"col": 5, "row": 3, "col_span": 5, "row_span": 5}

        with self.assertRaisesRegex(LayoutSpecValidationError, "must not overlap"):
            validate_template_layout_output(payload, allowed_asset_ids=self.assets)

    def test_rejects_arbitrary_css_or_html_fields(self) -> None:
        payload = _dog_cat_layout()
        payload["blocks"][0]["style"] = "color: red"

        with self.assertRaisesRegex(LayoutSpecValidationError, "unknown: style"):
            validate_template_layout_output(payload, allowed_asset_ids=self.assets)

    def test_rejects_canvas_in_model_output(self) -> None:
        payload = _dog_cat_layout()
        payload["canvas"] = {"columns": 12, "rows": 10}

        with self.assertRaisesRegex(LayoutSpecValidationError, "unknown: canvas"):
            validate_template_layout_output(payload, allowed_asset_ids=self.assets)

    def test_rejects_container_blocks(self) -> None:
        payload = _dog_cat_layout()
        payload["blocks"][0] = {
            "id": "background",
            "type": "container",
            "grid": {"col": 1, "row": 1, "col_span": 12, "row_span": 10},
        }

        with self.assertRaisesRegex(LayoutSpecValidationError, "text or image"):
            validate_template_layout_output(payload, allowed_asset_ids=self.assets)


def _dog_cat_layout() -> dict[str, object]:
    return {
        "blocks": [
            {
                "id": "b1",
                "type": "text",
                "content": "Cùng tìm hiểu chó và mèo",
                "grid": {"col": 1, "row": 1, "col_span": 12, "row_span": 1},
            },
            {
                "id": "b2",
                "type": "text",
                "content": "Con hãy quan sát hai bạn nhé!",
                "grid": {"col": 1, "row": 2, "col_span": 12, "row_span": 1},
            },
            {
                "id": "b3",
                "type": "image",
                "asset_id": "dog",
                "label": "Chó",
                "grid": {"col": 1, "row": 3, "col_span": 5, "row_span": 5},
            },
            {
                "id": "b4",
                "type": "image",
                "asset_id": "cat",
                "label": "Mèo",
                "grid": {"col": 7, "row": 3, "col_span": 5, "row_span": 5},
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
