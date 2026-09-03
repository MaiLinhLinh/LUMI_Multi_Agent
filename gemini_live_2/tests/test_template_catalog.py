"""Tests for CP9 reusable plan catalogs and their common compiler path."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gemini_live_2.catalogs.domains import DomainRegistry
from gemini_live_2.catalogs.templates import load_template_catalog
from gemini_live_2.catalogs.templates import TemplateCatalogError
from gemini_live_2.catalogs import LayoutTemplate, LayoutTemplateMaterializer, TemplateExtractor
from gemini_live_2.panel import DataBundle, GridRect, PanelCompiler, PlanBlock, PresentationPlan
from gemini_live_2.widgets import build_default_widget_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TemplateCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resources = DomainRegistry(PROJECT_ROOT / "domains").load("education")

    def test_catalog_exposes_semantic_entries_without_plan_paths(self) -> None:
        entries = self.resources.templates.for_plan_agent()
        self.assertIn({
            "id": "two_subject_comparison",
            "description": "So sánh trực quan hai đối tượng ngang hàng: mỗi bên có ảnh lớn và nhãn ngắn, phù hợp khi trẻ quan sát hai chủ thể.",
        }, entries)
        self.assertTrue(entries)
        self.assertTrue(all("layout_path" not in entry and "plan_path" not in entry for entry in entries))

    def test_catalogued_layout_materializes_before_using_the_same_compiler(self) -> None:
        layout = self.resources.templates.load_layout_template("two_subject_comparison")
        plan = LayoutTemplateMaterializer().materialize(
            template=layout,
            bindings={
                "$block_1_content": "Cùng quan sát hai bạn mèo nhé!",
                "$block_2_asset_id": "cat",
                "$block_2_label": "Mèo 1",
                "$block_3_asset_id": "cat",
                "$block_3_label": "Mèo 2",
            },
        )
        document = PanelCompiler(build_default_widget_registry()).compile_surface_document(
            surface_id="catalogued-surface",
            plan=plan,
            data_bundle=DataBundle(domain_id="education", data={}),
            domain_resources=self.resources,
        )
        self.assertEqual(plan.template_id, "two_subject_comparison")
        self.assertEqual(plan.blocks[1].props["asset_id"], "cat")
        self.assertEqual(plan.blocks[2].props["asset_id"], "cat")
        self.assertEqual([component.id for component in document.components], ["1", "2", "3"])
        self.assertEqual(set(document.anchor_map), {"a", "b", "c"})

    def test_unknown_template_id_is_rejected_at_the_trusted_loader_boundary(self) -> None:
        with self.assertRaisesRegex(TemplateCatalogError, "unknown template_id"):
            self.resources.templates.load_layout_template("does_not_exist")

    def test_catalog_persists_and_loads_a_layout_template(self) -> None:
        layout = TemplateExtractor(build_default_widget_registry()).extract(
            plan=PresentationPlan(
                domain_id="education",
                blocks=(PlanBlock("image", GridRect(1, 1, 12, 10), {"asset_id": "dog"}),),
            ),
            template_id="tm1",
            description="Một ảnh lớn.",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_text('{"domain_id":"education","templates":[]}', encoding="utf-8")
            catalog = load_template_catalog(
                catalog_path=catalog_path,
                domain_root=root,
                expected_domain_id="education",
            )

            saved_catalog = catalog.save_layout_template(layout)

            self.assertEqual(saved_catalog.load_layout_template("tm1"), layout)
            self.assertEqual(saved_catalog.next_generated_template_id(), "tm2")
            self.assertTrue((root / "layouts" / "tm1.layout.json").is_file())

            deleted_catalog = saved_catalog.delete_layout_template("tm1")

            self.assertFalse(deleted_catalog.contains("tm1"))
            self.assertFalse((root / "layouts" / "tm1.layout.json").exists())
            self.assertEqual(deleted_catalog.next_generated_template_id(), "tm1")


if __name__ == "__main__":
    unittest.main()
