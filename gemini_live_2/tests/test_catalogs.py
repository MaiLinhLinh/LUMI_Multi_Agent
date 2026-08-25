import json
import tempfile
import unittest
from pathlib import Path

from gemini_live_2.catalogs.assets import AssetCatalogError, load_asset_catalog
from gemini_live_2.catalogs.domains import DomainRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def test_education_manifest_loads_assets_without_domain_branching(self) -> None:
        resources = DomainRegistry(PROJECT_ROOT / "domains").load("education")
        self.assertEqual(resources.manifest.domain_id, "education")
        self.assertEqual(resources.manifest.allowed_widget_ids, ("text", "image", "object_group", "answer", "number_display"))
        self.assertIn("cô giáo thân thiện", resources.presentation_instruction)
        self.assertEqual([asset["id"] for asset in resources.assets.plan_agent_catalog()], ["dog", "cat"])
        self.assertNotIn("path", resources.assets.plan_agent_catalog()[0])

    def test_asset_catalog_rejects_mime_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "dog.jpg").write_bytes(b"placeholder")
            catalog = root / "catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "domain_id": "education",
                        "assets": [
                            {
                                "id": "dog",
                                "kind": "image",
                                "path": "dog.jpg",
                                "mime_type": "image/png",
                                "caption": "Dog",
                                "tags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(AssetCatalogError):
                load_asset_catalog(catalog_path=catalog, domain_root=root, expected_domain_id="education")

    def test_domain_registry_lists_declared_domains_only(self) -> None:
        registry = DomainRegistry(PROJECT_ROOT / "domains")
        self.assertEqual(registry.available_domain_ids(), ("education",))


if __name__ == "__main__":
    unittest.main()
