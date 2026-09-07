"""Server-side contract for the answer widget."""

from __future__ import annotations

from typing import Any, Mapping

from .registry import StageMapPolicy, WidgetAnchor, WidgetDefinition, WidgetPropDefinition, WidgetPropsError, WidgetStateDefinition


def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"value"}
    if unknown: raise WidgetPropsError(f"answer.props has unsupported fields: {sorted(unknown)}.")
    value = props.get("value")
    if not isinstance(value, str) or not value.strip(): raise WidgetPropsError("answer.value must be a non-empty string.")
    if len(value.strip()) > 80: raise WidgetPropsError("answer.value must not exceed 80 characters.")
    return {"value": value.strip()}


def definition(*, visibility_state: WidgetStateDefinition) -> WidgetDefinition:
    return WidgetDefinition(
        widget_id="answer", validate_props=_validate_props,
        anchor_policy=lambda _: (WidgetAnchor("answer", ("highlight", "circle")),),
        purpose="Hiển thị một đáp án số hoặc chữ ngắn; khi hidden hiển thị dấu ?, khi visible hiển thị value.",
        props=(WidgetPropDefinition("value", "string", True, "Đáp án hoặc từ ngắn cần hiển thị.", template_value_kind="binding"),),
        state_fields=(visibility_state,),
        stage_map_policy=StageMapPolicy(kind="answer", content_label="KẾT QUẢ", text_source="props.value", anchor_key="answer"),
    )
