"""Server-side contract for the selectable choice container widget."""

from __future__ import annotations

from typing import Any, Mapping

from .registry import StageMapPolicy, WidgetAnchor, WidgetDefinition, WidgetInteractionDefinition, WidgetPropsError, WidgetStateDefinition


def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    if props: raise WidgetPropsError("choice.props must be empty; the compiler assigns its interaction anchor.")
    return {}


def definition(*, visibility_state: WidgetStateDefinition, selected_state: WidgetStateDefinition) -> WidgetDefinition:
    return WidgetDefinition(
        widget_id="choice", validate_props=_validate_props,
        anchor_policy=lambda _: (WidgetAnchor("choice", ("highlight", "circle")),),
        purpose="Tạo một lựa chọn có thể chạm/chọn; hiển thị ảnh hoặc nhóm ở trên và nhãn/chữ ở dưới.",
        props=(), state_fields=(visibility_state, selected_state),
        allowed_child_widget_ids=("image", "text", "number_display", "object_group"),
        interactions=(WidgetInteractionDefinition("select", "Trẻ chạm hoặc chọn toàn bộ thẻ lựa chọn."),),
        stage_map_policy=StageMapPolicy(kind="container", anchor_key="choice", children_layout="vertical"),
    )
