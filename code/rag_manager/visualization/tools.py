"""Allow-listed LLM2 resource tools.

These functions are deliberately small and deterministic.  A LangChain tool
wrapper may call them, but the executor remains the authority that validates
requests and results.
"""

from __future__ import annotations

from typing import Any

from rag_manager.visualization.components import list_components
from rag_manager.visualization.registry import list_templates, read_template_metadata


TOOL_NAMES = {
    "search_templates",
    "search_base_templates",
    "get_template_metadata",
    "search_components",
    "get_component_metadata",
}


def validate_tool_request(request: dict[str, Any]) -> dict[str, Any]:
    """Validate the LLM2 tool-call envelope before execution."""

    if not isinstance(request, dict):
        raise ValueError("Tool request must be an object.")
    call_id = request.get("tool_call_id")
    name = request.get("tool_name")
    arguments = request.get("arguments")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("tool_call_id is required.")
    if name not in TOOL_NAMES:
        raise ValueError(f"Tool is not allow-listed: {name}")
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object.")
    required = {
        "get_template_metadata": "template_id",
        "get_component_metadata": "component_id",
    }.get(name)
    if required and not isinstance(arguments.get(required), str):
        raise ValueError(f"Tool {name} requires {required}.")
    return {"tool_call_id": call_id, "tool_name": name, "arguments": dict(arguments)}


def validate_tool_result(result: dict[str, Any], *, expected_call_id: str | None = None) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("Tool result must be an object.")
    if expected_call_id is not None and result.get("tool_call_id") != expected_call_id:
        raise ValueError("Tool result call ID does not match request.")
    if result.get("tool_name") not in TOOL_NAMES:
        raise ValueError("Tool result name is not allow-listed.")
    if result.get("status") not in {"ok", "not_found", "error"}:
        raise ValueError("Tool result status is invalid.")
    if not isinstance(result.get("candidates"), list):
        raise ValueError("Tool result candidates must be a list.")
    if result.get("status") == "not_found" and result["candidates"]:
        raise ValueError("not_found tool results must have empty candidates.")
    return result


def execute_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name not in TOOL_NAMES:
        return _error(tool_name, "tool_not_allowed")
    if not isinstance(arguments, dict):
        return _error(tool_name, "arguments_must_be_object")
    try:
        if tool_name == "search_templates":
            candidates = list_templates(domain=arguments.get("domain"))
            candidates = [item for item in candidates if _compatible(item, arguments)]
        elif tool_name == "search_base_templates":
            from rag_manager.visualization.template_agent import search_base_templates_by_metadata

            candidates = search_base_templates_by_metadata(
                domain=arguments.get("domain"),
                requirements={"presentation": {"preferred_patterns": arguments.get("keywords", [])}},
            )
        elif tool_name == "get_template_metadata":
            candidates = [read_template_metadata(str(arguments.get("template_id", "")))]
        elif tool_name == "search_components":
            candidates = list_components({"domain": arguments.get("domain")})
            slot = arguments.get("slot")
            if isinstance(slot, str):
                candidates = [item for item in candidates if slot in item.get("supported_slots", [])]
            candidates = [item for item in candidates if _compatible(item, arguments)]
        else:
            from rag_manager.visualization.components import read_component

            candidates = [read_component(str(arguments.get("component_id", ""))).metadata]
        status = "ok" if candidates else "not_found"
        return {
            "tool_call_id": str(arguments.get("tool_call_id", "")),
            "tool_name": tool_name,
            "status": status,
            "candidates": candidates,
            "error": None,
        }
    except Exception as exc:  # registry errors become structured tool errors
        return _error(tool_name, str(exc), arguments.get("tool_call_id", ""))


def _compatible(metadata: dict[str, Any], arguments: dict[str, Any]) -> bool:
    schema = arguments.get("schema_version")
    if isinstance(schema, str) and schema and schema not in metadata.get("schema_versions", []):
        return False
    raw_available = arguments.get("available_fields")
    available = set(item for item in raw_available if isinstance(item, str)) if isinstance(raw_available, list) else set()
    required = set(item for item in metadata.get("required_fields", []) if isinstance(item, str))
    return not required or not available or required.issubset(available)


def _error(tool_name: str, message: str, call_id: Any = "") -> dict[str, Any]:
    return {
        "tool_call_id": str(call_id),
        "tool_name": tool_name,
        "status": "error",
        "candidates": [],
        "error": {"code": "tool_error", "message": str(message)},
    }
