import unittest
from pathlib import Path

from gemini_live_2.catalogs.domains import DomainRegistry
from gemini_live_2.panel import (
    DataBundle,
    GridRect,
    PanelCompiler,
    PlanBlock,
    PresentationPlan,
    panel_client_payload,
    render_visual_stage_map,
)
from gemini_live_2.widgets import build_default_widget_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PanelRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        resources = DomainRegistry(PROJECT_ROOT / "domains").load("education")
        self.panel = PanelCompiler(build_default_widget_registry()).compile(
            panel_id="panel-fidelity",
            domain_resources=resources,
            data_bundle=DataBundle(domain_id="education", data={}),
            plan=PresentationPlan(
                domain_id="education",
                blocks=(
                    PlanBlock("text", GridRect(1, 1, 12, 1), {"content": "Dog and cat", "role": "title"}),
                    PlanBlock("image", GridRect(1, 3, 5, 5), {"asset_id": "dog", "label": "Dog"}),
                    PlanBlock("image", GridRect(7, 3, 5, 5), {"asset_id": "cat", "label": "Cat"}),
                ),
            ),
        )

    def test_ascii_map_describes_the_same_visible_regions_with_direct_anchors(self) -> None:
        stage_map = render_visual_stage_map(self.panel)
        self.assertIn("Dog and cat", stage_map)
        self.assertIn("ẢNH", stage_map)
        self.assertIn("dog", stage_map)
        self.assertIn("cat", stage_map)
        self.assertIn("[anchor: a]", stage_map)
        self.assertIn("[anchor: b]", stage_map)
        self.assertNotIn("@a", stage_map)
        self.assertNotIn("ANCHOR KEY", stage_map)
        self.assertNotIn("01 | A", stage_map)

        asset_row = next(line for line in stage_map.splitlines() if "dog" in line and "cat" in line)
        self.assertLess(asset_row.index("dog"), asset_row.index("cat"))

    def test_ascii_map_does_not_treat_an_image_alt_label_as_visible_text(self) -> None:
        stage_map = render_visual_stage_map(self.panel)
        self.assertNotIn("[ảnh dog]", stage_map.lower())
        self.assertNotIn('ảnh "Dog"', stage_map)

    def test_client_payload_has_only_used_browser_asset_urls(self) -> None:
        payload = panel_client_payload(
            self.panel,
            asset_urls={"dog": "/assets/domains/education/dog", "cat": "/assets/domains/education/cat", "unused": "/no"},
        )
        self.assertEqual(payload["ui_type"], "panel_ir")
        self.assertEqual(payload["panel"]["panel_id"], "panel-fidelity")
        self.assertEqual(payload["panel"]["blocks"][1]["widget_id"], "image")
        image_anchor = next(anchor for anchor in payload["panel"]["anchors"] if anchor["anchor_key"] == "image")
        self.assertEqual(image_anchor, {
            "anchor_id": "b",
            "block_id": "2",
            "anchor_key": "image",
            "target_id": "panel:panel-fidelity:block:2:anchor:image",
            "allowed_effect_ids": ["highlight", "circle"],
        })
        self.assertEqual(payload["assets"], [
            {"id": "cat", "url": "/assets/domains/education/cat"},
            {"id": "dog", "url": "/assets/domains/education/dog"},
        ])

    def test_client_payload_preserves_block_visibility(self) -> None:
        hidden_panel = PanelCompiler(build_default_widget_registry()).compile(
            panel_id="panel-hidden",
            domain_resources=DomainRegistry(PROJECT_ROOT / "domains").load("education"),
            data_bundle=DataBundle(domain_id="education", data={}),
            plan=PresentationPlan(
                domain_id="education",
                blocks=(
                    PlanBlock(
                        "image",
                        GridRect(1, 1, 5, 5),
                        {"asset_id": "dog"},
                        initial_visibility="hidden",
                    ),
                ),
            ),
        )
        payload = panel_client_payload(hidden_panel, asset_urls={"dog": "/assets/domains/education/dog"})
        self.assertEqual(payload["panel"]["blocks"][0]["visibility"], "hidden")
        self.assertEqual(payload["panel"]["blocks"][0]["props"], {})
        self.assertEqual(payload["assets"], [])

    def test_stage_map_redacts_hidden_content_then_exposes_it_after_reveal(self) -> None:
        hidden_panel = PanelCompiler(build_default_widget_registry()).compile(
            panel_id="panel-hidden-map",
            domain_resources=DomainRegistry(PROJECT_ROOT / "domains").load("education"),
            data_bundle=DataBundle(domain_id="education", data={}),
            plan=PresentationPlan(
                domain_id="education",
                blocks=(
                    PlanBlock(
                        "image",
                        GridRect(1, 1, 5, 5),
                        {"asset_id": "dog", "label": "Secret dog"},
                        initial_visibility="hidden",
                    ),
                    PlanBlock(
                        "answer",
                        GridRect(7, 1, 3, 3),
                        {"value": "42"},
                        initial_visibility="hidden",
                    ),
                ),
            ),
        )
        hidden_map = render_visual_stage_map(hidden_panel)
        self.assertIn("ĐANG ẨN", hidden_map)
        self.assertIn("[anchor: a]", hidden_map)
        self.assertIn("[anchor: b]", hidden_map)
        self.assertNotIn("Secret dog", hidden_map)
        self.assertNotIn("dog", hidden_map.lower())
        self.assertNotIn("42", hidden_map)

        revealed_map = render_visual_stage_map(
            hidden_panel.with_block_visibility(block_ids={"1", "2"}, visibility="visible")
        )
        self.assertIn("ẢNH", revealed_map)
        self.assertIn("dog", revealed_map)
        self.assertIn("KẾT QUẢ", revealed_map)
        self.assertIn("42", revealed_map)
        self.assertNotIn("Secret dog", revealed_map)


if __name__ == "__main__":
    unittest.main()
