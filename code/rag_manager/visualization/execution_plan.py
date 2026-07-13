"""Typed execution-plan contracts for visualization template work.

The LLM may propose a plan, but this module is the server-side boundary.  No
executor decision is made from free-form TODO text.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from rag_manager.visualization.validator import canonicalize_color, validate_color_value


class ExecutionPlanError(ValueError):
    """Raised when an LLM2 execution plan violates its contract."""


VALID_MODES = {"existing_template", "base_template"}
VALID_REF_TYPES = {"registry", "artifact"}
VALID_STYLE_PROPERTIES = {
    "background_color",
    "surface_color",
    "text_color",
    "accent_color",
    "border_color",
}


def validate_execution_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the public LLM2 execution-plan contract."""

    if not isinstance(plan, dict):
        raise ExecutionPlanError("Execution plan must be an object.")
    if plan.get("plan_version") != "1.0":
        raise ExecutionPlanError("Unsupported execution plan version.")

    target = plan.get("target")
    if not isinstance(target, dict):
        raise ExecutionPlanError("Execution plan target must be an object.")
    mode = target.get("mode")
    if mode not in VALID_MODES:
        raise ExecutionPlanError(f"Invalid execution target mode: {mode}")

    template_ref = _validate_ref(target.get("template_ref"), "template_ref", allow_none=True)
    base_ref = _validate_ref(target.get("base_ref"), "base_ref", allow_none=True)
    preserve = target.get("preserve_existing_structure")
    if not isinstance(preserve, bool):
        raise ExecutionPlanError("preserve_existing_structure must be boolean.")

    if mode == "existing_template":
        if template_ref is None or base_ref is not None or not preserve:
            raise ExecutionPlanError(
                "existing_template requires template_ref, no base_ref, and preservation=true."
            )
        if template_ref.get("kind") != "complete_template":
            raise ExecutionPlanError("existing_template requires a complete_template ref.")
    else:
        generation = plan.get("generation_plan")
        generation_base = generation.get("base") if isinstance(generation, dict) else None
        if base_ref is None and not isinstance(generation_base, dict):
            raise ExecutionPlanError("base_template requires base_ref or generation_plan.base.")
        if template_ref is not None or preserve:
            raise ExecutionPlanError("base_template cannot preserve an existing template.")
        if base_ref is not None and base_ref.get("kind") != "base_template":
            raise ExecutionPlanError("base_ref must reference a base_template.")

    lookup = plan.get("lookup_plan", {})
    if not isinstance(lookup, dict) or not all(
        isinstance(lookup.get(key, []), list)
        for key in ("templates", "base_templates", "components")
    ):
        raise ExecutionPlanError("lookup_plan must contain list fields.")

    resources = plan.get("resource_plan", {})
    if not isinstance(resources, dict):
        raise ExecutionPlanError("resource_plan must be an object.")
    reuse = resources.get("reuse_components", [])
    if not isinstance(reuse, list):
        raise ExecutionPlanError("resource_plan.reuse_components must be a list.")
    normalized_reuse = [_validate_component_ref(item) for item in reuse]
    if any(item is None for item in normalized_reuse):
        raise ExecutionPlanError("A reused component reference cannot be null.")

    generation = plan.get("generation_plan", {})
    if not isinstance(generation, dict):
        raise ExecutionPlanError("generation_plan must be an object.")
    generation_components = generation.get("components", [])
    if not isinstance(generation_components, list):
        raise ExecutionPlanError("generation_plan.components must be a list.")
    for item in generation_components:
        if not isinstance(item, dict) or not _nonempty_string(item.get("generation_key")):
            raise ExecutionPlanError("Generated components require generation_key.")
        if item.get("kind", "component") != "component":
            raise ExecutionPlanError("generation_plan.components may only generate components.")

    generation_base = generation.get("base")
    if generation_base is not None and (
        not isinstance(generation_base, dict)
        or not _nonempty_string(generation_base.get("generation_key"))
        or generation_base.get("kind", "base_template") != "base_template"
    ):
        raise ExecutionPlanError("generation_plan.base is invalid.")

    modification = plan.get("modification_plan", {})
    if not isinstance(modification, dict):
        raise ExecutionPlanError("modification_plan must be an object.")
    for key in ("content", "layout"):
        if not isinstance(modification.get(key, []), list):
            raise ExecutionPlanError(f"modification_plan.{key} has an invalid type.")
    if not isinstance(modification.get("style", {}), (dict, list)):
        raise ExecutionPlanError("modification_plan.style has an invalid type.")

    raw_style = modification.get("style", {})
    if isinstance(raw_style, dict):
        # Backward-compatible input. New plans must use a list so every style
        # change explicitly identifies the visual region being changed.
        target_id = modification.get("target_id")
        if raw_style and not _nonempty_string(target_id):
            raise ExecutionPlanError(
                "Style object form requires modification_plan.target_id; use the list form for multiple targets."
            )
        normalized_style: list[dict[str, Any]] = []
        for property_name, value in raw_style.items():
            normalized_style.append({"target_id": target_id, "property": property_name, "value": value})
    elif isinstance(raw_style, list):
        normalized_style = deepcopy(raw_style)
    else:
        raise ExecutionPlanError("modification_plan.style must be an object or list.")
    for item in normalized_style:
        if not isinstance(item, dict):
            raise ExecutionPlanError("Each style modification must be an object.")
        if not _nonempty_string(item.get("target_id")):
            raise ExecutionPlanError("Each style modification requires target_id.")
        property_name = item.get("property")
        value = item.get("value")
        if not _nonempty_string(property_name):
            raise ExecutionPlanError("Each style modification requires property.")
        if property_name not in VALID_STYLE_PROPERTIES:
            raise ExecutionPlanError(f"Style property is not allow-listed: {property_name}")
        if property_name.endswith("_color"):
            if not isinstance(value, str) or not validate_color_value(value):
                raise ExecutionPlanError(f"Invalid color value for {property_name}.")
            item["value"] = canonicalize_color(value)

    extracted_requirements = plan.get("requirements", {})
    if not isinstance(extracted_requirements, dict):
        raise ExecutionPlanError("requirements must be an object.")

    return {
        "plan_version": "1.0",
        "requirements": deepcopy(extracted_requirements),
        "target": {
            "mode": mode,
            "template_ref": deepcopy(template_ref),
            "base_ref": deepcopy(base_ref),
            "preserve_existing_structure": preserve,
        },
        "lookup_plan": {
            "templates": deepcopy(lookup.get("templates", [])),
            "base_templates": deepcopy(lookup.get("base_templates", [])),
            "components": deepcopy(lookup.get("components", [])),
        },
        "resource_plan": {"reuse_components": normalized_reuse},
        "generation_plan": {
            "base": deepcopy(generation_base),
            "components": deepcopy(generation_components),
        },
        "modification_plan": {
            "style": normalized_style,
            "content": deepcopy(modification.get("content", [])),
            "layout": deepcopy(modification.get("layout", [])),
        },
        "todo_list": [
            item for item in plan.get("todo_list", [])
            if isinstance(item, str) and item.strip()
        ] if isinstance(plan.get("todo_list", []), list) else [],
    }


def build_runtime_assembly_input(
    plan: dict[str, Any],
    *,
    generated_base_ref: dict[str, Any] | None = None,
    generated_component_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve generated refs into the runtime input consumed by LLM4/Assembler."""

    normalized = validate_execution_plan(plan)
    target = deepcopy(normalized["target"])
    generation = normalized["generation_plan"]
    if target["mode"] == "base_template" and target["base_ref"] is None:
        if generated_base_ref is None:
            raise ExecutionPlanError("Generated base ref is required at runtime.")
        target["base_ref"] = deepcopy(generated_base_ref)

    components = [deepcopy(item) for item in normalized["resource_plan"]["reuse_components"]]
    components.extend(deepcopy(generated_component_refs or []))
    for component in components:
        if component.get("ref_type") == "artifact" and component.get("status") != "validated":
            raise ExecutionPlanError("Only validated component artifacts may enter assembly_input.")
    if target["mode"] == "base_template" and target["base_ref"].get("ref_type") == "artifact":
        if target["base_ref"].get("status") != "validated":
            raise ExecutionPlanError("Only a validated base artifact may enter assembly_input.")
    return {"target": target, "components": components}


def _validate_ref(value: Any, label: str, *, allow_none: bool = False) -> dict[str, Any] | None:
    if value is None and allow_none:
        return None
    # Defensive normalization keeps the executor safe if an LLM emits the
    # short form, while the prompt still requires the full object contract.
    if isinstance(value, str) and value.strip():
        value = {
            "ref_type": "registry",
            "id": value.strip(),
            "kind": "complete_template" if label == "template_ref" else "base_template",
        }
    if not isinstance(value, dict):
        raise ExecutionPlanError(f"{label} must be an object or null.")
    if value.get("ref_type") not in VALID_REF_TYPES:
        raise ExecutionPlanError(f"{label}.ref_type is invalid.")
    if not _nonempty_string(value.get("id") or value.get("artifact_id")):
        raise ExecutionPlanError(f"{label} requires id or artifact_id.")
    if not _nonempty_string(value.get("kind")):
        raise ExecutionPlanError(f"{label}.kind is required.")
    if value.get("ref_type") == "artifact" and value.get("status") not in {"validated", None}:
        raise ExecutionPlanError(f"{label} artifact must be validated.")
    return dict(value)


def _validate_component_ref(value: Any) -> dict[str, Any]:
    """Normalize a component reference while keeping registry/artifact safety."""

    if isinstance(value, str) and value.strip():
        value = {"ref_type": "registry", "id": value.strip(), "kind": "component"}
    if not isinstance(value, dict):
        raise ExecutionPlanError("Component reference must be an object or ID.")
    normalized = dict(value)
    if not normalized.get("ref_type") and isinstance(normalized.get("id"), str):
        normalized["ref_type"] = "registry"
    if normalized.get("kind") is None:
        normalized["kind"] = "component"
    if normalized.get("kind") != "component":
        raise ExecutionPlanError("Component reference kind must be component.")
    result = _validate_ref(normalized, "reuse component")
    if result is None:
        raise ExecutionPlanError("Component reference cannot be null.")
    return result


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
