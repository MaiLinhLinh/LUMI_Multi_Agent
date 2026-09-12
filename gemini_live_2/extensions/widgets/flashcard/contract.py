"""Backend contract for the extension-owned two-sided flashcard widget."""

from __future__ import annotations

from typing import Any, Mapping

from gemini_live_2.widgets import (
    StageMapPolicy,
    StageMapTextSource,
    StageMapView,
    WidgetAnchor,
    WidgetAssetReferenceDefinition,
    WidgetDefinition,
    WidgetInteractionDefinition,
    WidgetPropDefinition,
    WidgetPropsError,
    WidgetStateDefinition,
)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetPropsError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _validate_props(props: Mapping[str, Any]) -> dict[str, Any]:
    if set(props) != {"front", "back"}:
        raise WidgetPropsError("flashcard.props must contain exactly front and back.")
    front, back = props.get("front"), props.get("back")
    if not isinstance(front, Mapping):
        raise WidgetPropsError("flashcard.front must be an object.")
    if set(front) - {"asset_id", "remote_image_result_id", "text"}:
        raise WidgetPropsError("flashcard.front has unsupported fields.")
    asset_id = front.get("asset_id")
    remote_image_result_id = front.get("remote_image_result_id")
    if (asset_id is None) == (remote_image_result_id is None):
        raise WidgetPropsError(
            "flashcard.front must contain exactly one of asset_id or remote_image_result_id."
        )
    if not isinstance(back, Mapping) or set(back) != {"word", "phonetic", "meaning"}:
        raise WidgetPropsError("flashcard.back must contain exactly word, phonetic and meaning.")
    normalized_front = {"text": _text(front.get("text"), "flashcard.front.text")}
    if asset_id is not None:
        normalized_front["asset_id"] = _text(asset_id, "flashcard.front.asset_id")
    else:
        normalized_front["remote_image_result_id"] = _text(
            remote_image_result_id, "flashcard.front.remote_image_result_id"
        )
    result = {
        "front": normalized_front,
        "back": {
            "word": _text(back.get("word"), "flashcard.back.word"),
            "phonetic": _text(back.get("phonetic"), "flashcard.back.phonetic"),
            "meaning": _text(back.get("meaning"), "flashcard.back.meaning"),
        },
    }
    for path, value in (
        ("flashcard.front.text", result["front"]["text"]),
        ("flashcard.back.word", result["back"]["word"]),
        ("flashcard.back.phonetic", result["back"]["phonetic"]),
        ("flashcard.back.meaning", result["back"]["meaning"]),
    ):
        if len(value) > 80:
            raise WidgetPropsError(f"{path} must not exceed 80 characters.")
    return result


_VISIBILITY = WidgetStateDefinition(
    name="visibility",
    value_type="string",
    default_value="visible",
    allowed_values=("visible", "hidden"),
    transitions={"visible": ("hidden",), "hidden": ("visible",)},
)
_FLIPPED = WidgetStateDefinition(name="flipped", value_type="boolean", default_value=False)


WIDGET_EXTENSION = WidgetDefinition(
    validate_props=_validate_props,
    anchor_policy=lambda _: (WidgetAnchor("card", ("highlight", "circle")),),
    purpose="Hiển thị thẻ từ vựng có thể lật giữa mặt ảnh và mặt kiến thức.",
    props=(
        WidgetPropDefinition(
            "front", "object", True, "Mặt trước gồm một ảnh và chữ ngắn thật sự hiển thị.",
            template_value_kind="binding",
            child_props=(
                WidgetPropDefinition("asset_id", "string", False, "ID ảnh từ Asset Catalog.", source="asset_catalog.id", template_value_kind="binding"),
                WidgetPropDefinition("remote_image_result_id", "string", False, "ID ảnh từ kết quả search_image của session hiện tại.", source="search_image.result_id", template_value_kind="binding"),
                WidgetPropDefinition("text", "string", True, "Chữ thật sự hiển thị ở mặt trước.", template_value_kind="binding"),
            ),
            exactly_one_of=("asset_id", "remote_image_result_id"),
            additional_properties=False,
        ),
        WidgetPropDefinition(
            "back", "object", True, "Mặt sau gồm word, phonetic và meaning đều hiển thị.",
            template_value_kind="binding",
            child_props=(
                WidgetPropDefinition("word", "string", True, "Từ vựng.", template_value_kind="binding"),
                WidgetPropDefinition("phonetic", "string", True, "Phiên âm.", template_value_kind="binding"),
                WidgetPropDefinition("meaning", "string", True, "Nghĩa của từ.", template_value_kind="binding"),
            ),
            additional_properties=False,
        ),
    ),
    state_fields=(_VISIBILITY, _FLIPPED),
    interactions=(WidgetInteractionDefinition("flip", "Chạm hoặc dùng bàn phím để lật giữa hai mặt thẻ.", state_rule={"flipped": {"op": "toggle"}}),),
    stage_map_policy=StageMapPolicy(
        kind="flashcard", anchor_key="card", views=(
            StageMapView("flipped", False, StageMapPolicy(kind="flashcard_front", content_label="ẢNH", asset_source="props.front.asset_id", asset_text_source="asset.caption", text_rendered=True, text_sources=(StageMapTextSource("CHỮ", "props.front.text", True),))),
            StageMapView("flipped", True, StageMapPolicy(kind="flashcard_back", text_rendered=True, text_sources=(StageMapTextSource("TỪ", "props.back.word", True), StageMapTextSource("PHIÊN ÂM", "props.back.phonetic", True), StageMapTextSource("NGHĨA", "props.back.meaning", True)))),
        ),
    ),
    asset_references=(WidgetAssetReferenceDefinition(path="props.front.asset_id", required=False),),
    declared_effect_ids=("highlight", "circle"),
)
