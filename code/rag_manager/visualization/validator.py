"""Deterministic validators for fill plans and generated HTML templates."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, TemplateSyntaxError, meta

from rag_manager.visualization.components import ComponentRegistryError, read_component
from rag_manager.visualization.paths import resolve_asset_path


class VisualizationValidationError(ValueError):
    """Raised when a visualization artifact fails deterministic validation."""


PLACEHOLDER_PATTERN = re.compile(r"{{\s*([^{}]+?)\s*}}")
INLINE_EVENT_PATTERN = re.compile(r"\son[a-zA-Z]+\s*=", re.IGNORECASE)
NETWORK_PATTERN = re.compile(
    r"(https?://|fetch\s*\(|XMLHttpRequest|navigator\.sendBeacon)",
    re.IGNORECASE,
)
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{3,4}$|^#[0-9a-fA-F]{6}$|^#[0-9a-fA-F]{8}$")
RGB_COLOR_PATTERN = re.compile(
    r"^rgba?\(\s*(?:\d{1,3}%?\s*,\s*){2}\d{1,3}%?(?:\s*,\s*(?:0|1|0?\.\d+|100%))?\s*\)$",
    re.IGNORECASE,
)
HSL_COLOR_PATTERN = re.compile(
    r"^hsla?\(\s*\d{1,3}(?:\.\d+)?\s*,\s*\d{1,3}%\s*,\s*\d{1,3}%"
    r"(?:\s*,\s*(?:0|1|0?\.\d+|100%))?\s*\)$",
    re.IGNORECASE,
)
TEMPLATE_PARSER = Environment(autoescape=True)
ALLOWED_TEMPLATE_VARIABLES = {
    "answer",
    "data",
    "page_title",
    "source",
}

# CSS named colors. Unknown alphabetic names are rejected rather than being
# inserted into CSS as arbitrary values.
CSS_COLOR_NAMES = {
    "aliceblue", "antiquewhite", "aqua", "aquamarine", "azure", "beige", "bisque",
    "black", "blanchedalmond", "blue", "blueviolet", "brown", "burlywood", "cadetblue",
    "chartreuse", "chocolate", "coral", "cornflowerblue", "cornsilk", "crimson", "cyan",
    "darkblue", "darkcyan", "darkgoldenrod", "darkgray", "darkgreen", "darkgrey", "darkkhaki",
    "darkmagenta", "darkolivegreen", "darkorange", "darkorchid", "darkred", "darksalmon",
    "darkseagreen", "darkslateblue", "darkslategray", "darkslategrey", "darkturquoise",
    "darkviolet", "deeppink", "deepskyblue", "dimgray", "dimgrey", "dodgerblue", "firebrick",
    "floralwhite", "forestgreen", "fuchsia", "gainsboro", "ghostwhite", "gold", "goldenrod",
    "gray", "green", "greenyellow", "grey", "honeydew", "hotpink", "indianred", "indigo",
    "ivory", "khaki", "lavender", "lavenderblush", "lawngreen", "lemonchiffon", "lightblue",
    "lightcoral", "lightcyan", "lightgray", "lightgreen", "lightgrey", "lightpink",
    "lightsalmon", "lightseagreen", "lightskyblue", "lightslategray", "lightslategrey", "lightsteelblue",
    "lightyellow", "lime", "limegreen", "linen", "magenta", "maroon", "mediumaquamarine",
    "mediumblue", "mediumorchid", "mediumpurple", "mediumseagreen", "mediumslateblue", "mediumspringgreen",
    "mediumturquoise", "mediumvioletred", "midnightblue", "mintcream", "mistyrose", "moccasin",
    "navajowhite", "navy", "oldlace", "olive", "olivedrab", "orange", "orangered", "orchid",
    "palegoldenrod", "palegreen", "paleturquoise", "palevioletred", "papayawhip", "peachpuff",
    "peru", "pink", "plum", "powderblue", "purple", "rebeccapurple", "red", "rosybrown",
    "royalblue", "saddlebrown", "salmon", "sandybrown", "seagreen", "seashell", "sienna",
    "silver", "skyblue", "slateblue", "slategray", "slategrey", "snow", "springgreen", "steelblue",
    "tan", "teal", "thistle", "tomato", "turquoise", "violet", "wheat", "white", "whitesmoke",
    "yellow", "yellowgreen",
}


def canonicalize_color(value: str) -> str:
    """Normalize natural-language color tokens to safe CSS color values."""

    normalized = " ".join(value.strip().casefold().split())
    if normalized.startswith("màu "):
        normalized = normalized[4:].strip()
    aliases = {
        "light pink": "lightpink",
        "hồng nhạt": "lightpink",
        "hong nhat": "lightpink",
        "hồng": "pink",
        "hong": "pink",
    }
    return aliases.get(normalized, normalized.replace(" ", ""))


def validate_color_value(value: str) -> bool:
    """Accept a complete CSS color token, never a CSS declaration."""

    if not isinstance(value, str):
        return False
    normalized = canonicalize_color(value)
    if any(token in normalized for token in (";", "{", "}", "url(", "var(", "--")):
        return False
    return bool(
        HEX_COLOR_PATTERN.fullmatch(normalized)
        or RGB_COLOR_PATTERN.fullmatch(normalized)
        or HSL_COLOR_PATTERN.fullmatch(normalized)
        or normalized in CSS_COLOR_NAMES
    )


def validate_fill_plan(
    fill_plan: dict[str, Any],
    *,
    available_fields: list[str] | None = None,
    artifact_components: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate a fill plan against base contract and component metadata."""

    if not isinstance(fill_plan, dict):
        raise VisualizationValidationError("Fill plan must be a dictionary.")

    base_id = _required_string(fill_plan, "base_template")
    base = _read_base_template(base_id)
    contract = base["contract"]
    slots_contract = contract.get("slots", {})
    if not isinstance(slots_contract, dict):
        raise VisualizationValidationError("Base template contract must define slots.")

    parameters = fill_plan.get("parameters", {})
    if not isinstance(parameters, dict):
        raise VisualizationValidationError("Fill plan parameters must be a dictionary.")
    _validate_required_parameters(parameters, contract)

    slots = fill_plan.get("slots", {})
    if not isinstance(slots, dict):
        raise VisualizationValidationError("Fill plan slots must be a dictionary.")

    available = set(available_fields or [])
    normalized_slots: dict[str, list[str]] = {}
    for slot_name, component_ids in slots.items():
        if slot_name not in slots_contract:
            raise VisualizationValidationError(f"Unknown slot for base template: {slot_name}")
        if not isinstance(component_ids, list):
            raise VisualizationValidationError(f"Slot {slot_name} must contain a list.")
        max_components = slots_contract[slot_name].get("max_components")
        if isinstance(max_components, int) and len(component_ids) > max_components:
            raise VisualizationValidationError(f"Slot {slot_name} exceeds max components.")

        normalized_ids = []
        for component_ref in component_ids:
            component, normalized_ref = _component_for_fill_ref(
                component_ref,
                artifact_components or {},
            )
            component_id = getattr(component, "component_id", None) or str(component_ref)
            supported_slots = _string_list(component.metadata.get("supported_slots"))
            if slot_name not in supported_slots:
                raise VisualizationValidationError(
                    f"Component {component_id} does not support slot {slot_name}."
                )
            missing_fields = [
                field
                for field in _string_list(component.metadata.get("required_fields"))
                if field not in available
            ]
            if missing_fields:
                raise VisualizationValidationError(
                    f"Component {component_id} is hidden because fields are missing: "
                    + ", ".join(missing_fields)
                )
            normalized_ids.append(normalized_ref)
        normalized_slots[slot_name] = normalized_ids

    for slot_name, slot_contract in slots_contract.items():
        if slot_contract.get("required") and not normalized_slots.get(slot_name):
            raise VisualizationValidationError(f"Required slot is empty: {slot_name}")

    return {
        "base_template": base_id,
        "parameters": parameters,
        "slots": normalized_slots,
    }


def _component_for_fill_ref(
    component_ref: Any,
    artifact_components: dict[str, dict[str, Any]],
) -> tuple[Any, str | dict[str, Any]]:
    if isinstance(component_ref, str):
        try:
            return read_component(component_ref), component_ref
        except ComponentRegistryError as exc:
            raise VisualizationValidationError(str(exc)) from exc
    if not isinstance(component_ref, dict):
        raise VisualizationValidationError("Component references must be IDs or reference objects.")
    ref_type = component_ref.get("ref_type")
    if ref_type == "registry" and isinstance(component_ref.get("id"), str):
        try:
            return read_component(component_ref["id"]), component_ref["id"]
        except ComponentRegistryError as exc:
            raise VisualizationValidationError(str(exc)) from exc
    artifact_id = component_ref.get("artifact_id")
    artifact = artifact_components.get(artifact_id) if isinstance(artifact_id, str) else None
    if ref_type == "artifact" and isinstance(artifact, dict):
        content = artifact.get("content", artifact)
        metadata = content.get("metadata", {}) if isinstance(content, dict) else {}
        html = content.get("component_html") if isinstance(content, dict) else None
        if not isinstance(html, str) or not isinstance(metadata, dict):
            raise VisualizationValidationError("Artifact component content is invalid.")
        component = type("ArtifactComponent", (), {
            "component_id": artifact_id,
            "html": html,
            "metadata": metadata,
        })()
        return component, dict(component_ref)
    raise VisualizationValidationError("Unknown or unvalidated component reference.")


def validate_template_syntax(html: str) -> None:
    """Validate simple template syntax expectations after assembly."""

    if not isinstance(html, str) or not html.strip():
        raise VisualizationValidationError("Template HTML must not be empty.")
    if "{{ slot." in html:
        raise VisualizationValidationError("Assembled template still contains slot placeholders.")
    try:
        TEMPLATE_PARSER.parse(html)
    except TemplateSyntaxError as exc:
        raise VisualizationValidationError(f"Invalid fillable template syntax: {exc}") from exc


def validate_placeholders(
    html: str,
    *,
    allowed_variables: set[str] | None = None,
) -> None:
    """Reject undeclared template roots outside the generic render contract."""

    try:
        parsed = TEMPLATE_PARSER.parse(html)
    except TemplateSyntaxError as exc:
        raise VisualizationValidationError(f"Invalid fillable template syntax: {exc}") from exc
    allowed = ALLOWED_TEMPLATE_VARIABLES if allowed_variables is None else allowed_variables
    unsupported = sorted(meta.find_undeclared_variables(parsed) - allowed)
    if unsupported:
        raise VisualizationValidationError(
            "Unsupported template placeholder root: " + ", ".join(unsupported)
        )


def validate_security(html: str) -> None:
    """Reject HTML patterns that can execute code or make network requests."""

    lowered = html.lower()
    if "<script" in lowered:
        raise VisualizationValidationError("Script tags are not allowed in visualization HTML.")
    if "<iframe" in lowered:
        raise VisualizationValidationError("Iframes are not allowed in visualization HTML.")
    if "<form" in lowered and "action=" in lowered:
        raise VisualizationValidationError("Form actions are not allowed in visualization HTML.")
    if INLINE_EVENT_PATTERN.search(html):
        raise VisualizationValidationError("Inline event handlers are not allowed.")
    if NETWORK_PATTERN.search(html):
        raise VisualizationValidationError("Network requests are not allowed in templates.")


def _read_base_template(base_id: str) -> dict[str, Any]:
    base_dir = resolve_asset_path("base_templates", base_id)
    if not base_dir.exists() or not base_dir.is_dir():
        raise VisualizationValidationError(f"Unknown base template: {base_id}")
    metadata = _read_json(base_dir / "metadata.json")
    contract = _read_json(base_dir / "contract.json")
    return {"metadata": metadata, "contract": contract}


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VisualizationValidationError(f"Missing required field: {key}")
    normalized = value.strip()
    if any(part in normalized for part in ("..", "/", "\\")):
        raise VisualizationValidationError(f"Invalid path-like field: {key}")
    return normalized


def _validate_required_parameters(parameters: dict[str, Any], contract: dict[str, Any]) -> None:
    parameter_contract = contract.get("parameters", {})
    if not isinstance(parameter_contract, dict):
        return
    for name, rules in parameter_contract.items():
        if isinstance(rules, dict) and rules.get("required") and name not in parameters:
            raise VisualizationValidationError(f"Missing required parameter: {name}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VisualizationValidationError(f"Missing JSON asset: {path}") from exc
    except json.JSONDecodeError as exc:
        raise VisualizationValidationError(f"Invalid JSON asset: {path}") from exc
    if not isinstance(data, dict):
        raise VisualizationValidationError(f"JSON asset must be an object: {path}")
    return data


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
