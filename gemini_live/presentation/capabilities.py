"""Loading and validation helpers for trusted template presentation metadata."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "domains"


def load_template_metadata(domain_id: str, template_id: str) -> dict[str, Any]:
    """Read metadata only from a registered domain template directory."""
    if not _TEMPLATE_ID_RE.fullmatch(domain_id):
        raise ValueError("invalid domain_id")
    if not _TEMPLATE_ID_RE.fullmatch(template_id):
        raise ValueError("invalid template_id")

    metadata_path = _TEMPLATE_ROOT / domain_id / "templates" / template_id / "metadata.json"
    if not metadata_path.is_file():
        raise ValueError(f"template metadata not found: {template_id}")

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"template metadata is unreadable: {template_id}") from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("id") != template_id
        or metadata.get("domain") != domain_id
    ):
        raise ValueError(f"template metadata is invalid: {template_id}")
    return metadata


def presentation_capabilities(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return only capability mappings with a semantic target declaration."""
    raw = metadata.get("presentation_capabilities")
    if not isinstance(raw, dict):
        return {}
    return {
        focus: capability
        for focus, capability in raw.items()
        if isinstance(focus, str)
        and isinstance(capability, dict)
        and (isinstance(capability.get("target_id"), str) or isinstance(capability.get("target_pattern"), str))
    }
