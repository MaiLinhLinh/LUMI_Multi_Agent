"""Assemble fillable base templates with deterministic components."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rag_manager.visualization.components import read_component
from rag_manager.visualization.paths import resolve_asset_path
from rag_manager.visualization.registry import lookup_template
from rag_manager.visualization.validator import (
    VisualizationValidationError,
    canonicalize_color,
    validate_color_value,
    validate_fill_plan,
    validate_placeholders,
    validate_security,
    validate_template_syntax,
)


def assemble_template_from_base(
    fill_plan: dict[str, Any],
    *,
    available_fields: list[str] | None = None,
    artifact_components: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Assemble a complete template from a base template and components."""

    normalized_plan = validate_fill_plan(
        fill_plan,
        available_fields=available_fields,
        artifact_components=artifact_components,
    )
    base_dir = resolve_asset_path("base_templates", normalized_plan["base_template"])
    metadata = _read_json(base_dir / "metadata.json")
    base_html = (base_dir / str(metadata.get("template_file", "base.html"))).read_text(
        encoding="utf-8"
    )

    html = base_html
    for parameter_name, value in normalized_plan["parameters"].items():
        html = html.replace(f"{{{{ {parameter_name} }}}}", str(value))

    for slot_name in _base_slots(metadata):
        component_html = "\n".join(
            _resolve_base_component(component_ref, artifact_components or {}).html
            for component_ref in normalized_plan["slots"].get(slot_name, [])
        )
        html = html.replace(f"{{{{ slot.{slot_name} }}}}", component_html)

    html = _apply_base_styles(html, fill_plan.get("style", []), metadata)

    validate_template_syntax(html)
    validate_placeholders(html)
    validate_security(html)
    return html


def assemble_existing_template(
    template_id: str,
    fill_plan: dict[str, Any],
    *,
    available_fields: list[str] | None = None,
    artifact_components: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Patch a complete template without reconstructing or dropping old content."""

    asset = lookup_template(template_id)
    metadata = asset.metadata
    html = asset.template_path.read_text(encoding="utf-8")
    operations = fill_plan.get("operations", []) if isinstance(fill_plan, dict) else []
    if not isinstance(operations, list):
        raise VisualizationValidationError("Existing-template fill plan operations must be a list.")
    extension_points = metadata.get("extension_points", [])
    if not isinstance(extension_points, list):
        raise VisualizationValidationError("Template extension_points must be a list.")
    points = {
        item.get("slot"): item
        for item in extension_points
        if isinstance(item, dict) and isinstance(item.get("slot"), str)
    }
    slot_counts: dict[str, int] = {}
    for operation in operations:
        if not isinstance(operation, dict):
            raise VisualizationValidationError("Fill plan operation must be an object.")
        op = operation.get("op")
        if op == "set_style":
            target_id = operation.get("target_id")
            target = _style_target(metadata, target_id)
            if target is None:
                raise VisualizationValidationError(f"Unknown style target: {target_id}")
            if operation.get("property") not in _string_list(target.get("properties")):
                raise VisualizationValidationError(
                    f"Style property is not allowed for target: {target_id}"
                )
            html = _apply_allowlisted_style(
                html,
                operation.get("property"),
                operation.get("value"),
                selector=target.get("selector"),
            )
            continue
        if op not in {"insert_component", "replace_component"}:
            raise VisualizationValidationError(f"Unsupported existing-template operation: {op}")
        slot = operation.get("slot")
        point = points.get(slot)
        if not isinstance(point, dict) or op not in point.get("allowed_operations", []):
            raise VisualizationValidationError(f"extension_point_missing: {slot}")
        slot_counts[slot] = slot_counts.get(slot, 0) + 1
        maximum = point.get("max_components")
        if isinstance(maximum, int) and slot_counts[slot] > maximum:
            raise VisualizationValidationError(f"Extension point exceeds max components: {slot}")
        component_ref = operation.get("component_ref")
        component_html, component_metadata = _resolve_component_ref(component_ref, artifact_components or {})
        if slot not in _string_list(component_metadata.get("supported_slots")):
            raise VisualizationValidationError(f"Component does not support slot: {slot}")
        required = _string_list(component_metadata.get("required_fields"))
        available = set(available_fields or [])
        missing = [field for field in required if field not in available]
        if missing:
            raise VisualizationValidationError("Component fields are missing: " + ", ".join(missing))
        selector = point.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise VisualizationValidationError(f"Extension point has no selector: {slot}")
        html = _insert_at_selector(html, selector, component_html, replace=op == "replace_component")
    validate_template_syntax(html)
    validate_placeholders(html)
    validate_security(html)
    return html


def assemble_template_from_base_source(
    fill_plan: dict[str, Any],
    *,
    base_html: str,
    contract: dict[str, Any],
    available_fields: list[str] | None = None,
    artifact_components: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Assemble an LLM-created base without adding it to packaged assets."""

    if not isinstance(base_html, str) or not base_html.strip():
        raise VisualizationValidationError("Generated base HTML must not be empty.")
    if not isinstance(contract, dict):
        raise VisualizationValidationError("Generated base contract must be an object.")

    parameters = fill_plan.get("parameters", {})
    slots = fill_plan.get("slots", {})
    slot_contract = contract.get("slots", {})
    if not isinstance(parameters, dict) or not isinstance(slots, dict) or not isinstance(slot_contract, dict):
        raise VisualizationValidationError("Generated base contract or fill plan is invalid.")

    available = set(available_fields or [])
    html = base_html
    for name, value in parameters.items():
        html = html.replace(f"{{{{ {name} }}}}", str(value))
    for slot_name, component_ids in slots.items():
        rule = slot_contract.get(slot_name)
        if not isinstance(rule, dict):
            raise VisualizationValidationError(f"Unknown generated base slot: {slot_name}")
        maximum = rule.get("max_components")
        if isinstance(maximum, int) and len(component_ids) > maximum:
            raise VisualizationValidationError(f"Slot {slot_name} exceeds max components.")
        rendered_components = []
        for component_ref in component_ids:
            component = _resolve_base_component(component_ref, artifact_components or {})
            component_id = getattr(component, "component_id", None) or str(component_ref)
            if slot_name not in _string_list(component.metadata.get("supported_slots")):
                raise VisualizationValidationError(
                    f"Component {component_id} does not support slot {slot_name}."
                )
            missing = [
                field
                for field in _string_list(component.metadata.get("required_fields"))
                if field not in available
            ]
            if missing:
                raise VisualizationValidationError(
                    f"Component {component_id} is hidden because fields are missing: {', '.join(missing)}"
                )
            rendered_components.append(component.html)
        html = html.replace(f"{{{{ slot.{slot_name} }}}}", "\n".join(rendered_components))

    html = _apply_base_styles(html, fill_plan.get("style", []), contract)

    validate_template_syntax(html)
    validate_placeholders(html)
    validate_security(html)
    return html


def _resolve_base_component(
    component_ref: Any,
    artifact_components: dict[str, dict[str, Any]],
) -> Any:
    if isinstance(component_ref, str):
        return read_component(component_ref)
    if isinstance(component_ref, dict) and component_ref.get("ref_type") == "registry":
        return read_component(component_ref.get("id"))
    if isinstance(component_ref, dict) and component_ref.get("ref_type") == "artifact":
        artifact = artifact_components.get(component_ref.get("artifact_id"))
        if not isinstance(artifact, dict):
            raise VisualizationValidationError("Generated component artifact is unavailable.")
        content = artifact.get("content", artifact)
        if not isinstance(content, dict):
            raise VisualizationValidationError("Generated component artifact is invalid.")
        return type("ArtifactComponent", (), {
            "html": content.get("component_html", ""),
            "metadata": content.get("metadata", {}),
        })()
    raise VisualizationValidationError("Invalid base component reference.")


def _apply_base_styles(
    html: str,
    styles: Any,
    metadata: dict[str, Any],
) -> str:
    if styles in (None, []):
        return html
    if not isinstance(styles, list):
        raise VisualizationValidationError("Base fill-plan style must be a list.")
    for operation in styles:
        if not isinstance(operation, dict):
            raise VisualizationValidationError("Base style operation must be an object.")
        target = _style_target(metadata, operation.get("target_id"))
        if target is None:
            raise VisualizationValidationError(f"Unknown base style target: {operation.get('target_id')}")
        if operation.get("property") not in _string_list(target.get("properties")):
            raise VisualizationValidationError("Base style property is not allowed.")
        html = _apply_allowlisted_style(
            html,
            operation.get("property"),
            operation.get("value"),
            selector=target.get("selector"),
        )
    return html


def _base_slots(metadata: dict[str, Any]) -> list[str]:
    slots = metadata.get("slots", [])
    return [slot for slot in slots if isinstance(slot, str)]


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _resolve_component_ref(
    component_ref: Any,
    artifact_components: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    if not isinstance(component_ref, dict):
        raise VisualizationValidationError("component_ref must be an object.")
    ref_type = component_ref.get("ref_type")
    if ref_type == "registry":
        component = read_component(str(component_ref.get("id", "")))
        return component.html, component.metadata
    if ref_type == "artifact":
        artifact_id = component_ref.get("artifact_id")
        artifact = artifact_components.get(str(artifact_id))
        if not isinstance(artifact, dict) or artifact.get("status") != "validated":
            raise VisualizationValidationError("Component artifact must be validated.")
        content = artifact.get("content", artifact)
        return str(content.get("component_html", "")), content.get("metadata", {})
    raise VisualizationValidationError("Unsupported component reference type.")


def _insert_at_selector(html: str, selector: str, component_html: str, *, replace: bool) -> str:
    match = re.fullmatch(r"\[data-slot=['\"]([^'\"]+)['\"]\]", selector.strip())
    if not match:
        raise VisualizationValidationError("Only data-slot extension selectors are supported.")
    slot = re.escape(match.group(1))
    opening = re.compile(rf"(<[a-zA-Z][^>]*data-slot=['\"]{slot}['\"][^>]*>)", re.IGNORECASE)
    if not opening.search(html):
        raise VisualizationValidationError(f"Extension point selector not found: {selector}")
    if replace:
        return opening.sub(rf"\1\n{component_html}", html, count=1)
    return opening.sub(rf"\1\n{component_html}", html, count=1)


def _apply_allowlisted_style(
    html: str,
    property_name: Any,
    value: Any,
    *,
    selector: str = "body",
) -> str:
    if property_name not in {"background_color", "surface_color", "text_color", "accent_color", "border_color"}:
        raise VisualizationValidationError("Style property is not allow-listed.")
    if not isinstance(value, str) or not validate_color_value(value):
        raise VisualizationValidationError("Style value is not a valid color.")
    css_value = canonicalize_color(value)
    css_property = {
        "background_color": "background",
        "surface_color": "background-color",
        "text_color": "color",
        "accent_color": "accent-color",
        "border_color": "border-color",
    }[property_name]
    if not isinstance(selector, str) or not selector.strip() or any(
        token in selector for token in ("<", ">", "{", "}", ";")
    ):
        raise VisualizationValidationError("Style target selector is unsafe.")
    css = f"<style data-generated-style=\"{property_name}\">{selector} {{ {css_property}: {css_value} !important; }}</style>"
    return html.replace("</head>", css + "\n</head>", 1) if "</head>" in html else css + html


def _style_target(metadata: dict[str, Any], target_id: Any) -> dict[str, Any] | None:
    targets = metadata.get("style_targets", [])
    if not isinstance(targets, list) or not isinstance(target_id, str):
        return None
    return next(
        (
            item for item in targets
            if isinstance(item, dict) and item.get("id") == target_id
        ),
        None,
    )
