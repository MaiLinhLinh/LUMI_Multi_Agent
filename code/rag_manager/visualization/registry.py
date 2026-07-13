"""Template registry for deterministic visualization assets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rag_manager.visualization.paths import resolve_asset_path


class TemplateRegistryError(ValueError):
    """Raised when a template cannot be resolved from local assets."""


@dataclass(frozen=True)
class TemplateAsset:
    """Resolved complete template asset."""

    template_id: str
    template_path: Path
    metadata_path: Path
    metadata: dict[str, Any]


def lookup_template(template_id: str) -> TemplateAsset:
    """Resolve a template by metadata id."""

    normalized_id = _normalize_template_id(template_id)
    for template in _iter_template_assets():
        if template.template_id == normalized_id:
            return template
    raise TemplateRegistryError(f"Unknown visualization template: {template_id}")


def read_template_metadata(template_id: str) -> dict[str, Any]:
    """Return metadata for a complete template."""

    return dict(lookup_template(template_id).metadata)


def list_templates(domain: str | None = None) -> list[dict[str, Any]]:
    """List registered complete templates, optionally filtered by domain."""

    templates = []
    for template in _iter_template_assets():
        metadata = dict(template.metadata)
        if domain is not None and metadata.get("domain") != domain:
            continue
        templates.append(metadata)
    return sorted(templates, key=lambda item: str(item.get("id", "")))


def recommend_templates(
    domain: str,
    schema_version: str,
    available_fields: list[str],
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Rank compatible templates using metadata and available fields."""

    filters = filters or {}
    available = set(available_fields)
    ranked: list[dict[str, Any]] = []

    for metadata in list_templates(domain=domain):
        if filters.get("kind") and metadata.get("kind") != filters["kind"]:
            continue
        schema_versions = _string_list(metadata.get("schema_versions"))
        if schema_versions and schema_version not in schema_versions:
            continue
        required_fields = _string_list(metadata.get("required_fields"))
        missing_required = [field for field in required_fields if field not in available]
        if missing_required:
            continue

        optional_fields = _string_list(metadata.get("optional_fields"))
        matched_optional = [field for field in optional_fields if field in available]
        score = 100 + (len(matched_optional) * 5) + len(required_fields)
        score += _requirements_score(metadata, filters.get("requirements"))
        recommendation = dict(metadata)
        recommendation["score"] = score
        recommendation["matched_optional_fields"] = matched_optional
        recommendation["missing_required_fields"] = missing_required
        ranked.append(recommendation)

    return sorted(ranked, key=lambda item: (-int(item["score"]), str(item.get("id", ""))))


def _iter_template_assets() -> list[TemplateAsset]:
    assets: list[TemplateAsset] = []
    # Auto-render uses only curated templates shipped in assets. Runtime
    # generated templates remain output artifacts until an explicit promotion
    # flow registers them; they must not silently alter domain template ranking.
    roots = [resolve_asset_path("templates")]
    metadata_paths = [
        metadata_path
        for root in roots
        if root.exists()
        for metadata_path in root.rglob("metadata.json")
    ]
    for metadata_path in sorted(metadata_paths):
        metadata = _read_json(metadata_path)
        template_id = _normalize_template_id(str(metadata.get("id", "")))
        template_file = str(metadata.get("template_file", "template.html"))
        template_path = metadata_path.parent / template_file
        if not template_id or not template_path.exists():
            continue
        assets.append(
            TemplateAsset(
                template_id=template_id,
                template_path=template_path,
                metadata_path=metadata_path,
                metadata=metadata,
            )
        )
    return assets


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateRegistryError(f"Invalid template metadata: {path}") from exc
    if not isinstance(data, dict):
        raise TemplateRegistryError(f"Template metadata must be an object: {path}")
    return data


def _normalize_template_id(template_id: str) -> str:
    normalized = template_id.strip()
    if not normalized or any(part in normalized for part in ("..", "/", "\\")):
        raise TemplateRegistryError(f"Invalid visualization template id: {template_id}")
    return normalized


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _requirements_score(metadata: dict[str, Any], requirements: Any) -> int:
    """Use user preferences for ranking without making compatibility fuzzy."""

    if not isinstance(requirements, dict):
        return 0
    haystack = " ".join(
        str(requirements.get(key, ""))
        for key in ("purpose", "content", "presentation", "style")
    ).casefold()
    metadata_text = " ".join(
        str(metadata.get(key, ""))
        for key in ("name", "description", "tags")
    ).casefold()
    keywords = {word for word in haystack.split() if len(word) > 3}
    return min(20, sum(1 for word in keywords if word in metadata_text))
