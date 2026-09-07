"""Server-side contract for the text widget."""

from __future__ import annotations

from typing import Any, Mapping

from .registry import (
    StageMapPolicy,
    WidgetAnchor,
    WidgetDefinition,
    WidgetPropDefinition,
    WidgetPropsError,
    WidgetStateDefinition,
)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetPropsError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"content", "role"}
    if unknown:
        raise WidgetPropsError(f"text.props has unsupported fields: {sorted(unknown)}.")
    content = _text(props.get("content"), "text.content")
    role = props.get("role", "body")
    if role not in {"title", "subtitle", "label", "body"}:
        raise WidgetPropsError("text.role must be title, subtitle, label or body.")
    return {"content": content, "role": role}


def _anchors(_: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
    return (WidgetAnchor(key="text", allowed_effect_ids=("highlight", "circle")),)


def definition(*, visibility_state: WidgetStateDefinition) -> WidgetDefinition:
    return WidgetDefinition(
        widget_id="text",
        validate_props=_validate_props,
        anchor_policy=_anchors,
        purpose="Hiển thị văn bản tự do như tiêu đề, nhãn hoặc nội dung ngắn.",
        props=(
            WidgetPropDefinition("content", "string", True, "Nội dung văn bản cần hiển thị.", template_value_kind="binding"),
            WidgetPropDefinition("role", "string", False, "Vai trò trình bày của văn bản.", allowed_values=("title", "subtitle", "label", "body")),
        ),
        state_fields=(visibility_state,),
        stage_map_policy=StageMapPolicy(kind="text", content_label="CHỮ", quote_text=True, text_source="props.content", anchor_key="text"),
    )
