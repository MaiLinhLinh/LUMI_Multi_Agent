import unittest
from dataclasses import replace
from pathlib import Path

from gemini_live_2.catalogs.domains import DomainRegistry
from gemini_live_2.panel import (
    ChoiceChild,
    DataBundle,
    GridRect,
    PanelCompiler,
    PlanBlock,
    PresentationPlan,
    render_visual_stage_map,
    surface_document_client_payload,
)
from gemini_live_2.widgets import build_default_widget_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PanelRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = build_default_widget_registry()
        self.resources = DomainRegistry(PROJECT_ROOT / "domains").load("education")
        self.document = PanelCompiler(self.registry).compile_surface_document(
            domain_resources=self.resources,
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
        stage_map = self._stage_map(self.document)
        self.assertIn("Dog and cat", stage_map)
        self.assertIn("ẢNH", stage_map)
        self.assertIn("Minh họa một chú chó", stage_map)
        self.assertIn("Minh họa một chú mèo", stage_map)
        self.assertIn("[anchor: a]", stage_map)
        self.assertIn("[anchor: b]", stage_map)
        self.assertNotIn("@a", stage_map)
        self.assertNotIn("ANCHOR KEY", stage_map)
        self.assertNotIn("01 | A", stage_map)

        asset_row = next(line for line in stage_map.splitlines() if "chú chó" in line and "chú mèo" in line)
        self.assertLess(asset_row.index("chú chó"), asset_row.index("chú mèo"))

    def test_ascii_map_does_not_treat_an_image_alt_label_as_visible_text(self) -> None:
        stage_map = self._stage_map(self.document)
        self.assertNotIn("[ảnh dog]", stage_map.lower())
        self.assertNotIn('ảnh "Dog"', stage_map)

    def test_client_payload_has_only_used_browser_asset_urls(self) -> None:
        payload = surface_document_client_payload(
            self.document,
            asset_urls={"dog": "/assets/domains/education/dog", "cat": "/assets/domains/education/cat", "unused": "/no"},
        )
        self.assertEqual(payload["ui_type"], "surface_document")
        self.assertTrue(payload["surface"]["surface_id"].startswith("panel-"))
        self.assertEqual(payload["surface"]["components"][1]["type"], "image")
        image_anchor = next(anchor for anchor in payload["surface"]["anchors"] if anchor["anchor_key"] == "image")
        self.assertEqual(image_anchor, {
            "anchor_id": "b",
            "component_id": "2",
            "anchor_key": "image",
            "allowed_effect_ids": ["highlight", "circle"],
        })
        self.assertEqual(payload["assets"], [
            {"id": "cat", "url": "/assets/domains/education/cat"},
            {"id": "dog", "url": "/assets/domains/education/dog"},
        ])

    def test_client_payload_preserves_block_visibility(self) -> None:
        hidden_document = PanelCompiler(self.registry).compile_surface_document(
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
        payload = surface_document_client_payload(
            hidden_document,
            asset_urls={"dog": "/assets/domains/education/dog"},
        )
        self.assertEqual(payload["surface"]["components"][0]["state"], {"visibility": "hidden"})
        self.assertEqual(payload["surface"]["components"][0]["props"], {})
        self.assertEqual(payload["assets"], [])

    def test_client_payload_includes_visible_choice_children_and_their_assets(self) -> None:
        choice_document = PanelCompiler(self.registry).compile_surface_document(
            domain_resources=DomainRegistry(PROJECT_ROOT / "domains").load("education"),
            data_bundle=DataBundle(domain_id="education", data={}),
            plan=PresentationPlan(
                domain_id="education",
                blocks=(
                    PlanBlock(
                        "choice",
                        GridRect(1, 1, 4, 5),
                        {},
                        children=(
                            ChoiceChild("image", {"asset_id": "cat"}),
                            ChoiceChild("text", {"content": "Mèo", "role": "label"}),
                        ),
                    ),
                ),
            ),
        )
        payload = surface_document_client_payload(
            choice_document,
            asset_urls={"cat": "/assets/domains/education/cat"},
        )
        component = payload["surface"]["components"][0]
        self.assertEqual(component["children"][0], {"type": "image", "props": {"asset_id": "cat"}})
        self.assertEqual(component["children"][1]["props"]["content"], "Mèo")
        self.assertEqual(payload["assets"], [{"id": "cat", "url": "/assets/domains/education/cat"}])
        stage_map = self._stage_map(choice_document)
        self.assertIn("ẢNH: Minh họa một chú mèo", stage_map)
        self.assertIn("Mèo", stage_map)
        self.assertIn("[anchor: a]", stage_map)

    def test_stage_map_redacts_hidden_content_then_exposes_it_after_reveal(self) -> None:
        hidden_document = PanelCompiler(self.registry).compile_surface_document(
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
        hidden_map = self._stage_map(hidden_document)
        self.assertIn("ĐANG ẨN", hidden_map)
        self.assertIn("[anchor: a]", hidden_map)
        self.assertIn("[anchor: b]", hidden_map)
        self.assertNotIn("Secret dog", hidden_map)
        self.assertNotIn("dog", hidden_map.lower())
        self.assertNotIn("42", hidden_map)

        revealed_map = self._stage_map(replace(
            hidden_document,
            components=tuple(replace(component, state={"visibility": "visible"}) for component in hidden_document.components),
        ))
        self.assertIn("ẢNH", revealed_map)
        self.assertIn("Minh họa một chú chó", revealed_map)
        self.assertIn("KẾT QUẢ", revealed_map)
        self.assertIn("42", revealed_map)
        self.assertNotIn("Secret dog", revealed_map)

    def test_object_group_uses_asset_caption_and_keeps_item_anchors_below_items(self) -> None:
        document = PanelCompiler(self.registry).compile_surface_document(
            domain_resources=self.resources,
            data_bundle=DataBundle(domain_id="education", data={}),
            plan=PresentationPlan(
                domain_id="education",
                blocks=(
                    PlanBlock("object_group", GridRect(1, 1, 8, 5), {"asset_id": "mango", "count": 2}),
                ),
            ),
        )
        stage_map = self._stage_map(document)
        self.assertIn("NHÓM: 2 × Một quả xoài", stage_map)
        item_line = next(line for line in stage_map.splitlines() if line.count("ẢNH: Một quả xoài") == 2)
        anchor_line = stage_map.splitlines()[stage_map.splitlines().index(item_line) + 1]
        self.assertIn("[anchor: b]", anchor_line)
        self.assertIn("[anchor: c]", anchor_line)
        self.assertIn("[anchor: a]", stage_map)

    def test_flashcard_stage_map_switches_only_the_rendered_face(self) -> None:
        document = PanelCompiler(self.registry).compile_surface_document(
            domain_resources=self.resources,
            data_bundle=DataBundle(domain_id="education", data={}),
            plan=PresentationPlan(
                domain_id="education",
                blocks=(
                    PlanBlock(
                        "flashcard", GridRect(3, 2, 8, 6),
                        {
                            "front": {"asset_id": "cat", "text": "Con mèo"},
                            "back": {"word": "CAT", "phonetic": "/kæt/", "meaning": "con mèo"},
                        },
                    ),
                ),
            ),
        )
        front_map = self._stage_map(document)
        self.assertIn("ẢNH: Minh họa một chú mèo", front_map)
        self.assertIn("CHỮ: “Con mèo”", front_map)
        self.assertNotIn("PHIÊN ÂM", front_map)
        self.assertIn("[anchor: a]", front_map)

        flipped_map = self._stage_map(replace(
            document,
            components=(replace(document.components[0], state={"visibility": "visible", "flipped": True}),),
        ))
        self.assertIn("TỪ: “CAT”", flipped_map)
        self.assertIn("PHIÊN ÂM: “/kæt/”", flipped_map)
        self.assertIn("NGHĨA: “con mèo”", flipped_map)
        self.assertNotIn("Minh họa một chú mèo", flipped_map)
        self.assertIn("[anchor: a]", flipped_map)

    def _stage_map(self, document):
        return render_visual_stage_map(
            document,
            widget_registry=self.registry,
            asset_catalog=self.resources.assets,
        )


if __name__ == "__main__":
    unittest.main()
