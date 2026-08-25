"""Tests for the non-Jinja Dynamic Grid presentation branch."""

from __future__ import annotations

import unittest
from pathlib import Path

from gemini_live.template_engine.layout_contract import layout_spec_to_dict, validate_layout_spec
from gemini_live.template_engine.template_llm import load_asset_catalog
from gemini_live.presentation import DynamicGridAsset, DynamicGridPresentation, PresentationPipeline


_EDUCATION_ASSET_CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "domains" / "education" / "templates" / "assets" / "catalog.json"
)


class _FailingRenderer:
    def render(self, **_kwargs: object) -> object:
        raise AssertionError("Dynamic Grid must not call the Jinja renderer.")


class DynamicGridPresentationTests(unittest.TestCase):
    def test_prepares_grid_payload_without_jinja(self) -> None:
        spec = validate_layout_spec(_dog_cat_layout(), allowed_asset_ids={"dog", "cat"})
        assets = tuple(
            DynamicGridAsset(id=asset.id, url=asset.public_url("/assets/education"))
            for asset in load_asset_catalog(_EDUCATION_ASSET_CATALOG_PATH)
        )
        pipeline = PresentationPipeline(renderer=_FailingRenderer())  # type: ignore[arg-type]

        prepared = pipeline.prepare_dynamic_grid(
            presentation=DynamicGridPresentation(
                domain_id="education",
                layout_spec=layout_spec_to_dict(spec),
                assets=assets,
            )
        )

        self.assertEqual(prepared.panel["ui_type"], "grid_layout")
        self.assertEqual(prepared.panel["domain_id"], "education")
        self.assertEqual(prepared.panel["assets"], [
            {"id": "dog", "url": "/assets/education/dog.jpg"},
            {"id": "cat", "url": "/assets/education/cat.jpg"},
        ])
        self.assertEqual(prepared.panel["presentation_targets"], {
            "b3": "dynamic-grid.block.b3",
            "b4": "dynamic-grid.block.b4",
        })
        self.assertEqual(prepared.panel_anchor_map, {
            "a": {
                "target_id": "dynamic-grid.block.b3",
                "allowed_effect_ids": ["highlight", "circle"],
            },
            "b": {
                "target_id": "dynamic-grid.block.b4",
                "allowed_effect_ids": ["highlight", "circle"],
            },
        })
        self.assertIn("Hình Chó [asset: dog] [anchor: a]", prepared.visual_stage_map)
        self.assertIn("Hình Mèo [asset: cat] [anchor: b]", prepared.visual_stage_map)
        self.assertNotIn("anchor: ", prepared.visual_stage_map.split("Văn bản:")[1].split("\n")[0])

    def test_rejects_an_asset_outside_the_public_education_namespace(self) -> None:
        pipeline = PresentationPipeline(renderer=_FailingRenderer())  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "public namespace"):
            pipeline.prepare_dynamic_grid(
                presentation=DynamicGridPresentation(
                    domain_id="education",
                    layout_spec=_dog_cat_layout(),
                    assets=(DynamicGridAsset(id="dog", url="/untrusted/dog.jpg"),),
                )
            )


def _dog_cat_layout() -> dict[str, object]:
    return {
        "canvas": {"columns": 12, "rows": 10},
        "blocks": [
            {
                "id": "b1", "type": "text", "content": "Cùng tìm hiểu chó và mèo",
                "grid": {"col": 1, "row": 1, "col_span": 12, "row_span": 1},
            },
            {
                "id": "b2", "type": "text", "content": "Con hãy quan sát hai bạn nhé!",
                "grid": {"col": 1, "row": 2, "col_span": 12, "row_span": 1},
            },
            {
                "id": "b3", "type": "image", "asset_id": "dog", "label": "Chó",
                "grid": {"col": 1, "row": 3, "col_span": 5, "row_span": 5},
            },
            {
                "id": "b4", "type": "image", "asset_id": "cat", "label": "Mèo",
                "grid": {"col": 7, "row": 3, "col_span": 5, "row_span": 5},
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
