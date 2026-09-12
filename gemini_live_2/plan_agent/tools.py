"""Public native-tool contracts for the Plan Agent.

This module intentionally defines schemas only. ``PlanAgent`` owns the
native-tool loop and dispatches calls to the backend services that implement
the requested work.
"""

from __future__ import annotations

from typing import Any

from google.genai import types

from gemini_live_2.gateway import CapabilityDescriptor


CALL_CAPABILITY = "call_capability"
DESCRIBE_WIDGETS = "describe_widgets"
DESCRIBE_TEMPLATE = "describe_template"
SEARCH_WEB = "search_web"
SEARCH_IMAGE = "search_image"


DESCRIBE_WIDGETS_TOOL: dict[str, Any] = {
    "name": DESCRIBE_WIDGETS,
    "description": "Lấy contract đầy đủ cho các widget được phép dùng trong surface mới.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["widget_ids"],
        "properties": {
            "widget_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
        },
    },
}


DESCRIBE_TEMPLATE_TOOL: dict[str, Any] = {
    "name": DESCRIBE_TEMPLATE,
    "description": "Return the binding contract for one reusable layout template.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["template_id"],
        "properties": {"template_id": {"type": "string"}},
    },
}


SEARCH_WEB_TOOL: dict[str, Any] = {
    "name": SEARCH_WEB,
    "description": "Tìm một nguồn web khi cần fact, giải thích hoặc nội dung cập nhật; trả title, snippet và source URL.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 400}},
    },
}


SEARCH_IMAGE_TOOL: dict[str, Any] = {
    "name": SEARCH_IMAGE,
    "description": "Tìm một ảnh khi Asset Catalog chưa có ảnh phù hợp; trả result_id và caption, không trả URL ảnh.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 400}},
    },
}


def call_capability_tool(capabilities: tuple[CapabilityDescriptor, ...]) -> dict[str, Any]:
    """Build the dynamic contract for capabilities permitted by this domain."""

    return {
        "name": CALL_CAPABILITY,
        "description": "Gọi một capability được cấp quyền để lấy dữ liệu tin cậy cần cho Surface Plan.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["capability_id", "arguments"],
            "properties": {
                "capability_id": {
                    "type": "string",
                    "enum": [capability.id for capability in capabilities],
                },
                "arguments": {"type": "object"},
            },
        },
    }


def definitions(capabilities: tuple[CapabilityDescriptor, ...]) -> tuple[dict[str, Any], ...]:
    """Return every native tool exposed for this planning request."""

    tools: tuple[dict[str, Any], ...] = (
        DESCRIBE_WIDGETS_TOOL,
        DESCRIBE_TEMPLATE_TOOL,
        SEARCH_WEB_TOOL,
        SEARCH_IMAGE_TOOL,
    )
    return (*tools, call_capability_tool(capabilities)) if capabilities else tools


def gemini_tools(capabilities: tuple[CapabilityDescriptor, ...]) -> types.Tool:
    """Adapt the contracts to the Gemini SDK representation."""

    return types.Tool(functionDeclarations=[
        types.FunctionDeclaration(
            name=tool["name"],
            description=tool["description"],
            parametersJsonSchema=tool["parameters"],
        )
        for tool in definitions(capabilities)
    ])


def cerebras_tools(capabilities: tuple[CapabilityDescriptor, ...]) -> list[dict[str, Any]]:
    """Adapt the same contracts to Cerebras' OpenAI-compatible format."""

    return [{"type": "function", "function": tool} for tool in definitions(capabilities)]
