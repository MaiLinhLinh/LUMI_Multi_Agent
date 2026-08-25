from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from gemini_live_2.catalogs.domains import DomainRegistry
from gemini_live_2.gateway import (
    CapabilityDescriptor,
    DomainCapability,
    DomainGateway,
    GatewayConfigurationError,
    GatewayPermissionError,
)
from gemini_live_2.panel.contracts import DataAlias, DataBundle


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DomainGatewayTests(unittest.TestCase):
    def test_education_asset_only_flow_exposes_no_capabilities(self) -> None:
        gateway = DomainGateway(DomainRegistry(PROJECT_ROOT / "domains"))

        self.assertEqual(gateway.capability_catalog("education"), ())
        self.assertEqual(gateway.empty_bundle("education"), DataBundle(domain_id="education", data={}))

    def test_only_manifest_granted_capability_can_execute(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_domain(root, "education", capabilities=["lookup_lesson"])
            self._write_domain(root, "weather", capabilities=["get_weather"])
            gateway = DomainGateway(DomainRegistry(root))
            gateway.register(
                DomainCapability(
                    domain_id="education",
                    descriptor=CapabilityDescriptor(
                        id="lookup_lesson",
                        description="Load verified lesson data.",
                        input_schema={"type": "object"},
                    ),
                    handler=lambda arguments: DataBundle(
                        domain_id="education",
                        data={"lesson": {"topic": arguments["topic"]}},
                        aliases=(
                            DataAlias(
                                id="$topic",
                                path=("lesson", "topic"),
                                description="Verified lesson topic.",
                            ),
                        ),
                    ),
                )
            )
            gateway.register(
                DomainCapability(
                    domain_id="weather",
                    descriptor=CapabilityDescriptor(
                        id="get_weather",
                        description="Load verified weather.",
                        input_schema={"type": "object"},
                    ),
                    handler=lambda arguments: DataBundle(domain_id="weather", data={"ok": True}),
                )
            )

            bundle = gateway.execute(
                domain_id="education", capability_id="lookup_lesson", arguments={"topic": "animals"}
            )

            self.assertEqual(bundle.data["lesson"]["topic"], "animals")
            self.assertEqual([alias.id for alias in bundle.alias_catalog], ["$topic"])
            self.assertEqual([item.id for item in gateway.capability_catalog("education")], ["lookup_lesson"])
            with self.assertRaises(GatewayPermissionError):
                gateway.execute(domain_id="education", capability_id="get_weather", arguments={})

    def test_manifest_capability_requires_registered_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_domain(root, "education", capabilities=["lookup_lesson"])
            gateway = DomainGateway(DomainRegistry(root))

            with self.assertRaises(GatewayConfigurationError):
                gateway.capability_catalog("education")

    def test_gateway_loads_future_domain_tools_py_lazily(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_domain(root, "education", capabilities=["lookup_lesson"])
            (root / "education" / "tools.py").write_text(
                "from gemini_live_2.gateway import CapabilityDescriptor, DomainCapability\n"
                "from gemini_live_2.panel.contracts import DataBundle\n"
                "CAPABILITIES = (DomainCapability(\n"
                "    domain_id='education',\n"
                "    descriptor=CapabilityDescriptor(id='lookup_lesson', description='Load lesson.', input_schema={'type': 'object'}),\n"
                "    handler=lambda arguments: DataBundle(domain_id='education', data={'topic': arguments['topic']}),\n"
                "),)\n",
                encoding="utf-8",
            )
            gateway = DomainGateway(DomainRegistry(root))

            self.assertEqual([item.id for item in gateway.capability_catalog("education")], ["lookup_lesson"])
            self.assertEqual(
                gateway.execute(
                    domain_id="education", capability_id="lookup_lesson", arguments={"topic": "animals"}
                ).data,
                {"topic": "animals"},
            )

    @staticmethod
    def _write_domain(root: Path, domain_id: str, *, capabilities: list[str]) -> None:
        domain_root = root / domain_id
        asset_root = domain_root / "assets"
        asset_root.mkdir(parents=True)
        (asset_root / "sample.png").write_bytes(b"not-a-real-image")
        (domain_root / "manifest.json").write_text(
            json.dumps(
                {
                    "domain_id": domain_id,
                    "asset_catalog_path": "assets/catalog.json",
                    "presentation_prompt_path": "prompt.py",
                    "presentation_prompt_constant": "PRESENTATION_INSTRUCTION",
                    "allowed_widget_ids": [],
                    "tool_capabilities": capabilities,
                }
            ),
            encoding="utf-8",
        )
        (domain_root / "prompt.py").write_text(
            'PRESENTATION_INSTRUCTION = "Prompt tạm cho test."\n', encoding="utf-8"
        )
        (asset_root / "catalog.json").write_text(
            json.dumps(
                {
                    "domain_id": domain_id,
                    "assets": [
                        {
                            "id": "sample",
                            "kind": "image",
                            "path": "assets/sample.png",
                            "mime_type": "image/png",
                            "caption": "Sample",
                            "tags": ["sample"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
