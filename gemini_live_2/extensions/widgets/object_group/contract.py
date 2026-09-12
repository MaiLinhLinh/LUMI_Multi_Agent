"""Contract for the object_group extension."""

from typing import Any, Mapping

from gemini_live_2.widgets import (
    StageMapPolicy, WidgetAnchor, WidgetAssetReferenceDefinition, WidgetDefinition,
    WidgetPropDefinition, WidgetPropsError, WidgetStateDefinition,
)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise WidgetPropsError(f"{field} must be a non-empty string.")
    return value.strip()

def _positive(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1: raise WidgetPropsError(f"{field} must be a positive integer.")
    return value

def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"asset_id", "count", "label"}
    if unknown: raise WidgetPropsError(f"object_group.props has unsupported fields: {sorted(unknown)}.")
    result: dict[str, Any] = {"asset_id": _text(props.get("asset_id"), "object_group.asset_id"), "count": _positive(props.get("count"), "object_group.count")}
    if props.get("label") is not None: result["label"] = _text(props["label"], "object_group.label")
    return result

_VISIBILITY = WidgetStateDefinition(name="visibility", value_type="string", default_value="visible", allowed_values=("visible", "hidden"), transitions={"visible": ("hidden",), "hidden": ("visible",)})

WIDGET_EXTENSION = WidgetDefinition(
    validate_props=_validate_props,
    anchor_policy=lambda props: (WidgetAnchor("group", ("highlight", "circle")), *(WidgetAnchor(f"item_{index}", ("highlight", "circle")) for index in range(1, props["count"] + 1))),
    purpose="Hiển thị một nhóm nhiều bản sao của cùng asset.",
    props=(WidgetPropDefinition("asset_id", "string", True, "ID của asset được lặp trong nhóm.", source="asset_catalog.id", template_value_kind="binding"), WidgetPropDefinition("count", "integer", True, "Số lượng bản sao của asset trong nhóm.", minimum=1, template_value_kind="binding"), WidgetPropDefinition("label", "string", False, "Nhãn ngắn cho cả nhóm.", template_value_kind="binding")),
    state_fields=(_VISIBILITY,),
    stage_map_policy=StageMapPolicy(kind="object_group", content_label="NHÓM", asset_source="props.asset_id", asset_text_source="asset.caption", count_source="props.count", anchor_key="group", item_anchor_prefix="item_", text_rendered=False),
    asset_references=(WidgetAssetReferenceDefinition(path="props.asset_id"),),
    declared_effect_ids=("highlight", "circle"),
)
