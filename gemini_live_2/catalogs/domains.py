"""Domain manifests: declarative capability boundaries for the framework core."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .assets import AssetCatalog, AssetCatalogError, load_asset_catalog
from .templates import (
    TemplateCatalog,
    TemplateCatalogError,
    empty_template_catalog,
    load_template_catalog,
)


class ManifestError(ValueError):
    pass


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string.")
    return value.strip()


def _relative_path(value: object, field: str) -> Path | None:
    if value is None:
        return None
    path = Path(_text(value, field))
    if path.is_absolute() or ".." in path.parts:
        raise ManifestError(f"{field} must be a safe relative path.")
    return path


@dataclass(frozen=True, slots=True)
class DomainManifest:
    domain_id: str
    asset_catalog_path: Path
    allowed_widget_ids: tuple[str, ...]
    template_catalog_path: Path | None = None
    tool_capabilities: tuple[str, ...] = ()
    presentation_prompt_path: Path | None = None
    presentation_prompt_constant: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _text(self.domain_id, "manifest.domain_id"))
        if not isinstance(self.asset_catalog_path, Path):
            raise ManifestError("manifest.asset_catalog_path must be a Path.")
        for field_name in ("allowed_widget_ids", "tool_capabilities"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or not all(isinstance(value, str) and value.strip() for value in values):
                raise ManifestError(f"manifest.{field_name} must be an array of non-empty strings.")
            if len(values) != len(set(values)):
                raise ManifestError(f"manifest.{field_name} contains duplicates.")
        has_prompt_path = self.presentation_prompt_path is not None
        has_prompt_constant = self.presentation_prompt_constant is not None
        if has_prompt_path != has_prompt_constant:
            raise ManifestError(
                "manifest.presentation_prompt_path and "
                "manifest.presentation_prompt_constant must be declared together."
            )
        if not has_prompt_path:
            raise ManifestError(
                "manifest must declare presentation_prompt_path and presentation_prompt_constant."
            )
        if has_prompt_constant:
            object.__setattr__(
                self,
                "presentation_prompt_constant",
                _text(self.presentation_prompt_constant, "manifest.presentation_prompt_constant"),
            )

    def for_plan_agent(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "allowed_widget_ids": list(self.allowed_widget_ids),
            "tool_capabilities": list(self.tool_capabilities),
        }


@dataclass(frozen=True, slots=True)
class DomainResources:
    """Trusted resources loaded from one domain directory.

    ``domain_root`` is server-only.  It exists so the Gateway can load that
    domain's executable ``tools.py`` without exposing paths to either model.
    """

    domain_root: Path
    manifest: DomainManifest
    assets: AssetCatalog
    templates: TemplateCatalog
    presentation_instruction: str


class DomainRegistry:
    """Loads declarative resources. It never branches on a concrete domain id."""

    def __init__(self, domains_root: Path) -> None:
        self._domains_root = domains_root.resolve()

    def available_domain_ids(self) -> tuple[str, ...]:
        if not self._domains_root.is_dir():
            return ()
        return tuple(sorted(path.name for path in self._domains_root.iterdir() if (path / "manifest.json").is_file()))

    def load(self, domain_id: str) -> DomainResources:
        safe_id = _text(domain_id, "domain_id")
        domain_root = (self._domains_root / safe_id).resolve()
        try:
            domain_root.relative_to(self._domains_root)
        except ValueError as exc:
            raise ManifestError("domain_id resolves outside domains root.") from exc
        manifest_path = domain_root / "manifest.json"
        try:
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"cannot load domain manifest {safe_id}: {exc}") from exc
        if not isinstance(raw_manifest, Mapping):
            raise ManifestError("domain manifest root must be an object.")
        manifest = self._parse_manifest(raw_manifest, domain_root)
        if manifest.domain_id != safe_id:
            raise ManifestError("manifest.domain_id must match its domain directory.")
        try:
            assets = load_asset_catalog(
                catalog_path=domain_root / manifest.asset_catalog_path,
                domain_root=domain_root,
                expected_domain_id=manifest.domain_id,
            )
        except AssetCatalogError as exc:
            raise ManifestError(str(exc)) from exc
        try:
            templates = (
                load_template_catalog(
                    catalog_path=domain_root / manifest.template_catalog_path,
                    domain_root=domain_root,
                    expected_domain_id=manifest.domain_id,
                )
                if manifest.template_catalog_path is not None
                else empty_template_catalog(domain_id=manifest.domain_id, domain_root=domain_root)
            )
        except TemplateCatalogError as exc:
            raise ManifestError(str(exc)) from exc
        presentation_instruction = self._load_presentation_instruction(manifest, domain_root)
        return DomainResources(
            domain_root=domain_root,
            manifest=manifest,
            assets=assets,
            templates=templates,
            presentation_instruction=presentation_instruction,
        )

    @staticmethod
    def _parse_manifest(data: Mapping[str, Any], domain_root: Path) -> DomainManifest:
        raw_widgets = data.get("allowed_widget_ids")
        raw_capabilities = data.get("tool_capabilities", [])
        if not isinstance(raw_widgets, list) or not isinstance(raw_capabilities, list):
            raise ManifestError("manifest widget types and tool capabilities must be arrays.")
        return DomainManifest(
            domain_id=data.get("domain_id"),
            asset_catalog_path=_relative_path(data.get("asset_catalog_path"), "manifest.asset_catalog_path"),
            template_catalog_path=_relative_path(data.get("template_catalog_path"), "manifest.template_catalog_path"),
            allowed_widget_ids=tuple(raw_widgets),
            tool_capabilities=tuple(raw_capabilities),
            presentation_prompt_path=_relative_path(
                data.get("presentation_prompt_path"), "manifest.presentation_prompt_path"
            ),
            presentation_prompt_constant=data.get("presentation_prompt_constant"),
        )

    @staticmethod
    def _load_presentation_instruction(manifest: DomainManifest, domain_root: Path) -> str:
        """Load a domain-owned prompt constant without a concrete-domain branch."""

        assert manifest.presentation_prompt_path is not None
        prompt_path = (domain_root / manifest.presentation_prompt_path).resolve()
        try:
            prompt_path.relative_to(domain_root)
        except ValueError as exc:
            raise ManifestError("presentation prompt resolves outside its domain.") from exc
        if not prompt_path.is_file():
            raise ManifestError("presentation prompt file does not exist.")
        module_name = f"gemini_live_2_domain_prompt_{manifest.domain_id}"
        spec = importlib.util.spec_from_file_location(module_name, prompt_path)
        if spec is None or spec.loader is None:
            raise ManifestError("cannot load presentation prompt module.")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise ManifestError(f"cannot execute presentation prompt module: {exc}") from exc
        value = getattr(module, manifest.presentation_prompt_constant or "", None)
        if not isinstance(value, str) or not value.strip():
            raise ManifestError("presentation prompt constant must be a non-empty string.")
        return value.strip()
