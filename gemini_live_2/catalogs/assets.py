"""Validated, domain-scoped asset catalogs.

The catalog is the only asset inventory later given to the Plan Agent.  Paths
remain server-side here; the browser URL is deliberately a renderer concern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class AssetCatalogError(ValueError):
    pass


_MEDIA_BY_SUFFIX = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssetCatalogError(f"{field} must be a non-empty string.")
    return value.strip()


@dataclass(frozen=True, slots=True)
class AssetDescriptor:
    id: str
    kind: str
    caption: str
    path: Path
    mime_type: str
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "asset.id"))
        object.__setattr__(self, "kind", _text(self.kind, "asset.kind"))
        object.__setattr__(self, "caption", _text(self.caption, "asset.caption"))
        if not isinstance(self.path, Path) or not self.path.is_file():
            raise AssetCatalogError(f"asset.path does not exist: {self.path}")
        expected_mime = _MEDIA_BY_SUFFIX.get(self.path.suffix.lower())
        if expected_mime is None:
            raise AssetCatalogError(f"unsupported asset format: {self.path.suffix}")
        if self.mime_type != expected_mime:
            raise AssetCatalogError(
                f"asset.mime_type must be {expected_mime!r} for {self.path.name!r}."
            )
        if not isinstance(self.tags, tuple) or not all(isinstance(tag, str) and tag.strip() for tag in self.tags):
            raise AssetCatalogError("asset.tags must be an array of non-empty strings.")
        object.__setattr__(self, "tags", tuple(tag.strip() for tag in self.tags))

    def for_plan_agent(self) -> dict[str, Any]:
        """Return the safe semantic inventory; never leak server filesystem paths."""
        return {"id": self.id, "kind": self.kind, "caption": self.caption, "tags": list(self.tags)}


@dataclass(frozen=True, slots=True)
class AssetCatalog:
    domain_id: str
    assets: tuple[AssetDescriptor, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _text(self.domain_id, "catalog.domain_id"))
        if not isinstance(self.assets, tuple) or not self.assets:
            raise AssetCatalogError("catalog.assets must contain at least one asset.")
        if not all(isinstance(asset, AssetDescriptor) for asset in self.assets):
            raise AssetCatalogError("catalog.assets contains an invalid descriptor.")
        ids = [asset.id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise AssetCatalogError("catalog.assets contains duplicate asset ids.")

    def get(self, asset_id: str) -> AssetDescriptor:
        for asset in self.assets:
            if asset.id == asset_id:
                return asset
        raise AssetCatalogError(f"unknown asset id: {asset_id}")

    def plan_agent_catalog(self) -> list[dict[str, Any]]:
        return [asset.for_plan_agent() for asset in self.assets]


def load_asset_catalog(*, catalog_path: Path, domain_root: Path, expected_domain_id: str) -> AssetCatalog:
    """Load a JSON catalog whose paths are strictly contained by its domain root."""
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetCatalogError(f"cannot load asset catalog {catalog_path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise AssetCatalogError("asset catalog root must be an object.")
    domain_id = _text(payload.get("domain_id"), "catalog.domain_id")
    if domain_id != expected_domain_id:
        raise AssetCatalogError("asset catalog domain_id does not match the manifest.")
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        raise AssetCatalogError("catalog.assets must be an array.")

    resolved_root = domain_root.resolve()
    descriptors: list[AssetDescriptor] = []
    for index, item in enumerate(raw_assets):
        if not isinstance(item, Mapping):
            raise AssetCatalogError(f"catalog.assets[{index}] must be an object.")
        relative_path = Path(_text(item.get("path"), f"catalog.assets[{index}].path"))
        if relative_path.is_absolute():
            raise AssetCatalogError("asset.path must be relative to the domain root.")
        asset_path = (resolved_root / relative_path).resolve()
        try:
            asset_path.relative_to(resolved_root)
        except ValueError as exc:
            raise AssetCatalogError("asset.path escapes the domain root.") from exc
        raw_tags = item.get("tags", [])
        if not isinstance(raw_tags, list):
            raise AssetCatalogError("asset.tags must be an array.")
        descriptors.append(
            AssetDescriptor(
                id=item.get("id"),
                kind=item.get("kind"),
                caption=item.get("caption"),
                path=asset_path,
                mime_type=item.get("mime_type"),
                tags=tuple(raw_tags),
            )
        )
    return AssetCatalog(domain_id=domain_id, assets=tuple(descriptors))
