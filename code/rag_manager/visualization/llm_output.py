"""Parse and validate Template Agent LLM JSON outputs."""

from __future__ import annotations

import json
import re
from typing import Any


class LlmOutputError(ValueError):
    """Raised when LLM output cannot be parsed or validated."""


def parse_llm_json_response(text: str) -> dict[str, Any]:
    """Parse a JSON object from plain or fenced LLM output."""

    if not isinstance(text, str):
        raise LlmOutputError("LLM response must be text.")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LlmOutputError(f"Invalid LLM JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LlmOutputError("LLM JSON output must be an object.")
    return parsed


def validate_requirements_output(output: dict[str, Any]) -> dict[str, Any]:
    """Validate the conversational requirement-gate output.

    The legacy flat fields remain accepted so existing prompt/client fixtures
    can be migrated without breaking the rest of the template pipeline.
    """

    _require_object(output, "requirements")
    status = _string(output.get("status")) or (
        "needs_clarification" if output.get("has_sufficient_template_description") is False else "ready"
    )
    if status not in {"ready", "needs_clarification", "cancelled"}:
        raise LlmOutputError(f"Invalid requirements status: {status}")

    requirements = output.get("requirements")
    if not isinstance(requirements, dict):
        requirements = {
            "purpose": {"description": _string(output.get("user_goal"))},
            "content": {
                "primary": _string_list(output.get("data_focus")),
                "secondary": [],
            },
            "presentation": {
                "description": "",
                "preferred_patterns": [],
            },
            "style": {"preferences": _string_list(output.get("style_preferences"))},
            "constraints": {},
        }

    missing = _string_list(output.get("missing_information"))
    question = _string(output.get("clarifying_question"))
    if status == "needs_clarification" and not question:
        raise LlmOutputError("Clarification output must contain clarifying_question.")
    return {
        "status": status,
        "requirements": requirements,
        "missing_information": missing,
        "clarifying_question": question,
        "user_goal": _string(output.get("user_goal")),
        "domain": _string(output.get("domain")),
        "mode": _string(output.get("mode")),
        "style_preferences": _string_list(output.get("style_preferences")),
        "data_focus": _string_list(output.get("data_focus")),
    }


def validate_semantic_router_output(output: dict[str, Any]) -> dict[str, Any]:
    """Validate the minimal route result, with legacy visualization support."""

    _require_object(output, "semantic router")
    route = output.get("route")
    if route in {"domain", "social", "template"}:
        domain_request = _string(output.get("domain_request")) or None
        if route == "domain" and domain_request is None:
            raise LlmOutputError("Domain route requires domain_request.")
        if route in {"social", "template"} and domain_request is not None:
            raise LlmOutputError(
                f"{route.capitalize()} route requires domain_request=null."
            )
        return {
            "route": route,
            "domain_request": domain_request,
        }

    # Compatibility for stored fixtures/clients using the former semantic
    # visualization contract. The production prompt no longer emits it.
    status = _string(output.get("status")) or "ready"
    if status not in {"ready", "needs_clarification", "cancelled"}:
        raise LlmOutputError(f"Invalid semantic router status: {status}")

    if route not in {"visualize", None}:
        raise LlmOutputError(f"Invalid semantic router route: {route}")

    template = output.get("template", {})
    if not isinstance(template, dict):
        raise LlmOutputError("Semantic router template must be an object.")
    action = template.get("action")
    if action not in {"show_options", "select_existing", "design_template", "cancel", None}:
        raise LlmOutputError(f"Invalid semantic template action: {action}")

    template_id = template.get("template_id")
    if template_id is not None and not isinstance(template_id, str):
        raise LlmOutputError("Semantic template_id must be a string or null.")
    selection_index = template.get("selection_index")
    if selection_index is not None and (
        not isinstance(selection_index, int) or selection_index < 1
    ):
        raise LlmOutputError("Semantic selection_index must be a positive integer or null.")
    requirements = template.get("requirements", {})
    if not isinstance(requirements, dict):
        raise LlmOutputError("Semantic requirements must be an object.")
    keywords = template.get("extracted_keywords", [])
    if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
        raise LlmOutputError("Semantic extracted_keywords must be a string list.")
    missing = output.get("missing_information", [])
    if not isinstance(missing, list) or not all(isinstance(item, str) for item in missing):
        raise LlmOutputError("Semantic missing_information must be a string list.")
    question = output.get("clarifying_question")
    if question is not None and not isinstance(question, str):
        raise LlmOutputError("clarifying_question must be a string or null.")
    if status == "needs_clarification" and not _string(question):
        raise LlmOutputError("Clarification requires clarifying_question.")

    return {
        "status": status,
        "route": route,
        "domain_request": _string(output.get("domain_request")) or None,
        "template": {
            "action": action,
            "source": _string(template.get("source")) or "none",
            "template_id": template_id,
            "selection_index": selection_index,
            "requirements": requirements,
            "extracted_keywords": keywords,
        },
        "missing_information": missing,
        "clarifying_question": _string(question) or None,
    }


def validate_strategy_output(
    output: dict[str, Any],
    *,
    template_ids: set[str],
    base_ids: set[str],
) -> dict[str, Any]:
    """Validate strategy output against template/base candidates."""

    _require_object(output, "strategy")
    strategy = output.get("strategy")
    existing_strategies = {"existing_template", "use_existing_template"}
    assemble_strategies = {
        "assemble_base",
        "use_base_as_is",
        "use_base_with_template_level_adjustments",
        "fork_or_customize_existing_template",
    }
    if strategy in existing_strategies:
        template_id = output.get("template_id")
        if template_id not in template_ids:
            raise LlmOutputError(f"Invalid template_id from LLM: {template_id}")
        return {
            "strategy": "existing_template",
            "template_id": template_id,
            "reason": _string(output.get("reason")),
        }
    if strategy in assemble_strategies:
        base_template = output.get("base_template")
        if base_template not in base_ids:
            raise LlmOutputError(f"Invalid base_template from LLM: {base_template}")
        return {
            "strategy": "assemble_base",
            "base_template": base_template,
            "reason": _string(output.get("reason")),
        }
    if strategy == "create_new_base_template":
        return {
            "strategy": strategy,
            "reason": _string(output.get("reason")),
        }
    raise LlmOutputError(
        "Strategy must be an existing template, an existing base, or create_new_base_template."
    )


def validate_component_selection_output(
    output: dict[str, Any],
    *,
    visible_component_ids: set[str],
) -> dict[str, list[str]]:
    """Validate selected component IDs against visible candidates."""

    slots = output.get("slots", output)
    if not isinstance(slots, dict):
        raise LlmOutputError("Component selection must contain slots.")
    selected: dict[str, list[str]] = {}
    for slot, component_ids in slots.items():
        if not isinstance(slot, str) or not isinstance(component_ids, list):
            raise LlmOutputError("Component selection slots must map to lists.")
        selected[slot] = []
        for component_id in component_ids:
            if component_id not in visible_component_ids:
                raise LlmOutputError(f"Invalid component_id from LLM: {component_id}")
            selected[slot].append(component_id)
    return selected


def validate_todo_list_output(output: dict[str, Any]) -> list[str]:
    """Validate todo-list output."""

    todos = output.get("todo_list")
    if not isinstance(todos, list):
        raise LlmOutputError("Todo output must contain todo_list.")
    return [item for item in todos if isinstance(item, str) and item.strip()]


def validate_fill_plan_output(
    output: dict[str, Any],
    *,
    base_ids: set[str],
    visible_component_ids: set[str],
) -> dict[str, Any]:
    """Validate fill plan IDs against base/component candidates."""

    base_template = output.get("base_template")
    if base_template not in base_ids:
        raise LlmOutputError(f"Invalid base_template from LLM: {base_template}")
    parameters = output.get("parameters", {})
    if not isinstance(parameters, dict):
        raise LlmOutputError("Fill plan parameters must be an object.")
    slots = output.get("slots")
    if not isinstance(slots, dict):
        raise LlmOutputError("Fill plan must contain slots.")
    validated_slots = validate_component_selection_output(
        {"slots": slots},
        visible_component_ids=visible_component_ids,
    )
    return {
        "base_template": base_template,
        "parameters": parameters,
        "slots": validated_slots,
    }


def _require_object(output: dict[str, Any], label: str) -> None:
    if not isinstance(output, dict):
        raise LlmOutputError(f"LLM {label} output must be an object.")


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
