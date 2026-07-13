"""Component registry for fillable visualization templates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag_manager.visualization.paths import resolve_asset_path


class ComponentRegistryError(ValueError):
    """Raised when a component cannot be resolved from local assets."""


@dataclass(frozen=True)
class ComponentAsset:
    """Resolved component asset."""

    component_id: str
    component_path: Path
    metadata_path: Path
    metadata: dict[str, Any]
    html: str


def list_components(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """List component metadata, optionally filtered by domain, slot, or tags."""

    filters = filters or {}
    components = []
    for component in _iter_component_assets():
        metadata = dict(component.metadata)
        if not _matches_filters(metadata, filters):
            continue
        components.append(metadata)
    return sorted(components, key=lambda item: str(item.get("id", "")))


def read_component(component_id: str) -> ComponentAsset:
    """Resolve a component by metadata id."""

    normalized_id = _normalize_component_id(component_id)
    for component in _iter_component_assets():
        if component.component_id == normalized_id:
            return component
    raise ComponentRegistryError(f"Unknown visualization component: {component_id}")


def search_components_by_metadata(
    *,
    domain: str | None = None,
    slot: str | None = None,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Search component metadata using deterministic asset metadata filters."""

    filters: dict[str, Any] = {}
    if domain is not None:
        filters["domain"] = domain
    if slot is not None:
        filters["slot"] = slot
    if tags:
        filters["tags"] = tags
    return list_components(filters)


def filter_visible_components(
    components: list[dict[str, Any]],
    available_fields: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Split components into visible and hidden groups based on required fields."""

    available = set(available_fields)
    visible = []
    hidden = []
    for component in components:
        required_fields = _string_list(component.get("required_fields"))
        missing_fields = [field for field in required_fields if field not in available]
        annotated = dict(component)
        annotated["missing_fields"] = missing_fields
        if missing_fields:
            hidden.append(annotated)
        else:
            visible.append(annotated)
    return {"visible_components": visible, "hidden_components": hidden}


def _iter_component_assets() -> list[ComponentAsset]:
    components_root = resolve_asset_path("components")
    if not components_root.exists():
        return []

    assets: list[ComponentAsset] = []
    for metadata_path in sorted(components_root.rglob("metadata.json")):
        metadata = _read_json(metadata_path)
        component_id = _normalize_component_id(str(metadata.get("id", "")))
        component_file = str(metadata.get("component_file", "component.html"))
        component_path = metadata_path.parent / component_file
        if not component_id or not component_path.exists():
            continue
        assets.append(
            ComponentAsset(
                component_id=component_id,
                component_path=component_path,
                metadata_path=metadata_path,
                metadata=metadata,
                html=component_path.read_text(encoding="utf-8"),
            )
        )
    return assets


def _matches_filters(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    domain = filters.get("domain")
    if domain is not None and metadata.get("domain") != domain:
        return False
    slot = filters.get("slot")
    if slot is not None and slot not in _string_list(metadata.get("supported_slots")):
        return False
    tags = filters.get("tags")
    if tags:
        component_tags = set(_string_list(metadata.get("tags")))
        if not set(_string_list(tags)).issubset(component_tags):
            return False
    return True


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ComponentRegistryError(f"Invalid component metadata: {path}") from exc
    if not isinstance(data, dict):
        raise ComponentRegistryError(f"Component metadata must be an object: {path}")
    return data


def _normalize_component_id(component_id: str) -> str:
    normalized = component_id.strip()
    if not normalized or any(part in normalized for part in ("..", "/", "\\")):
        raise ComponentRegistryError(f"Invalid visualization component id: {component_id}")
    return normalized


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]

