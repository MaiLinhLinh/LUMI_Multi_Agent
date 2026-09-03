"""Widget contracts independent of any domain, panel renderer or LLM.

The registry deliberately owns widget props and visual permissions.  A plan
only names a widget and supplies its props; later the compiler asks the widget
for the anchors it is allowed to materialize.  Therefore the Plan Agent never
creates DOM targets or anchor identifiers directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


class WidgetPropsError(ValueError):
    """Raised when block props do not meet a widget's public contract."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WidgetPropsError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WidgetPropsError(f"{field_name} must be a positive integer.")
    return value


@dataclass(frozen=True, slots=True)
class WidgetAnchor:
    """A widget-local anchor request; the compiler assigns final anchor IDs."""

    key: str
    allowed_effect_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _text(self.key, "widget anchor key"))
        if not self.allowed_effect_ids:
            raise WidgetPropsError("widget anchor must allow at least one effect.")
        normalized = tuple(_text(effect, "widget anchor effect") for effect in self.allowed_effect_ids)
        if len(normalized) != len(set(normalized)):
            raise WidgetPropsError("widget anchor effects must be unique.")
        object.__setattr__(self, "allowed_effect_ids", normalized)


AnchorPolicy = Callable[[Mapping[str, Any]], tuple[WidgetAnchor, ...]]
PropsValidator = Callable[[Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class WidgetPropDefinition:
    """A serializable public contract for one widget property."""

    name: str
    value_type: str
    required: bool
    description: str
    source: str | None = None
    allowed_values: tuple[str, ...] = ()
    minimum: int | None = None
    template_value_kind: str = "structural"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "widget prop name"))
        object.__setattr__(self, "value_type", _text(self.value_type, "widget prop type"))
        object.__setattr__(self, "description", _text(self.description, "widget prop description"))
        if self.source is not None:
            object.__setattr__(self, "source", _text(self.source, "widget prop source"))
        values = tuple(_text(value, "widget prop allowed value") for value in self.allowed_values)
        if len(values) != len(set(values)):
            raise WidgetPropsError("widget prop allowed values must be unique.")
        object.__setattr__(self, "allowed_values", values)
        if self.minimum is not None and self.minimum < 0:
            raise WidgetPropsError("widget prop minimum must not be negative.")
        if self.template_value_kind not in {"structural", "binding"}:
            raise WidgetPropsError(
                "widget prop template_value_kind must be structural or binding."
            )

    def to_public_contract(self) -> dict[str, Any]:
        contract: dict[str, Any] = {
            "type": self.value_type,
            "required": self.required,
            "description": self.description,
            "template_value_kind": self.template_value_kind,
        }
        if self.source is not None:
            contract["source"] = self.source
        if self.allowed_values:
            contract["allowed_values"] = list(self.allowed_values)
        if self.minimum is not None:
            contract["minimum"] = self.minimum
        return contract


@dataclass(frozen=True, slots=True)
class WidgetStateDefinition:
    """One runtime-state field a widget explicitly permits Runtime to change."""

    name: str
    value_type: str
    allowed_values: tuple[str, ...] = ()
    transitions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "widget state name"))
        object.__setattr__(self, "value_type", _text(self.value_type, "widget state type"))
        values = tuple(_text(value, "widget state allowed value") for value in self.allowed_values)
        if len(values) != len(set(values)):
            raise WidgetPropsError("widget state allowed values must be unique.")
        object.__setattr__(self, "allowed_values", values)
        normalized_transitions: dict[str, tuple[str, ...]] = {}
        for source, targets in self.transitions.items():
            source_value = _text(source, "widget state transition source")
            if not isinstance(targets, tuple):
                raise WidgetPropsError("widget state transition targets must be a tuple.")
            normalized_transitions[source_value] = tuple(
                _text(target, "widget state transition target") for target in targets
            )
        object.__setattr__(self, "transitions", normalized_transitions)

    def validate_change(self, *, current_value: Any, next_value: Any) -> Any:
        if self.value_type == "string":
            if not isinstance(next_value, str):
                raise WidgetPropsError(f"state.{self.name} must be a string.")
            if self.allowed_values and next_value not in self.allowed_values:
                raise WidgetPropsError(
                    f"state.{self.name} must be one of {list(self.allowed_values)}."
                )
        elif self.value_type == "boolean":
            if not isinstance(next_value, bool):
                raise WidgetPropsError(f"state.{self.name} must be a boolean.")
        else:  # Future fields must add an explicit validator instead of guessing.
            raise WidgetPropsError(f"unsupported state type '{self.value_type}'.")
        allowed_targets = self.transitions.get(str(current_value))
        if allowed_targets is not None and next_value not in allowed_targets:
            raise WidgetPropsError(
                f"state.{self.name} cannot transition from '{current_value}' to '{next_value}'."
            )
        return next_value


@dataclass(frozen=True, slots=True)
class WidgetDefinition:
    """Registered widget behaviour shared by every domain that enables it."""

    widget_id: str
    validate_props: PropsValidator
    anchor_policy: AnchorPolicy
    purpose: str
    props: tuple[WidgetPropDefinition, ...]
    state_fields: tuple[WidgetStateDefinition, ...] = ()
    allowed_child_widget_ids: tuple[str, ...] = ()
    interaction_event: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "widget_id", _text(self.widget_id, "widget_id"))
        object.__setattr__(self, "purpose", _text(self.purpose, "widget purpose"))
        names = tuple(prop.name for prop in self.props)
        if len(names) != len(set(names)):
            raise WidgetPropsError("widget prop names must be unique.")
        state_names = tuple(state.name for state in self.state_fields)
        if len(state_names) != len(set(state_names)):
            raise WidgetPropsError("widget state field names must be unique.")
        children = tuple(_text(value, "widget child widget_id") for value in self.allowed_child_widget_ids)
        if len(children) != len(set(children)):
            raise WidgetPropsError("widget child widget_ids must be unique.")
        object.__setattr__(self, "allowed_child_widget_ids", children)
        if self.interaction_event is not None:
            object.__setattr__(self, "interaction_event", _text(self.interaction_event, "widget interaction event"))

    def validate(self, props: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(props, Mapping):
            raise WidgetPropsError(f"{self.widget_id}.props must be an object.")
        return self.validate_props(props)

    def anchors_for(self, props: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
        return self.anchor_policy(self.validate(props))

    def public_props_contract(self) -> dict[str, dict[str, Any]]:
        return {prop.name: prop.to_public_contract() for prop in self.props}

    def public_contract(self) -> dict[str, Any]:
        """Detailed contract returned after Plan Agent discovers a widget."""

        contract: dict[str, Any] = {"props": self.public_props_contract()}
        if self.state_fields:
            contract["state_fields"] = {
                state.name: {
                    "type": state.value_type,
                    "allowed_values": list(state.allowed_values),
                    "transitions": {
                        source: list(targets) for source, targets in state.transitions.items()
                    },
                }
                for state in self.state_fields
            }
        if self.allowed_child_widget_ids:
            contract["allowed_child_widget_ids"] = list(self.allowed_child_widget_ids)
        if self.interaction_event is not None:
            contract["interaction_event"] = self.interaction_event
        return contract

    def validate_state_changes(
        self,
        *,
        current_state: Mapping[str, Any],
        changes: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(current_state, Mapping) or not isinstance(changes, Mapping) or not changes:
            raise WidgetPropsError("state changes must be a non-empty object.")
        definitions = {field.name: field for field in self.state_fields}
        unsupported = set(changes) - set(definitions)
        if unsupported:
            raise WidgetPropsError(
                f"{self.widget_id} does not allow state fields: {sorted(unsupported)}."
            )
        updated = dict(current_state)
        for field_name, next_value in changes.items():
            definition = definitions[field_name]
            updated[field_name] = definition.validate_change(
                current_value=updated.get(field_name), next_value=next_value
            )
        return updated


class WidgetRegistry:
    """Explicit registry: domains opt in through their manifest, core does not branch."""

    def __init__(self, definitions: tuple[WidgetDefinition, ...] = ()) -> None:
        self._definitions: dict[str, WidgetDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: WidgetDefinition) -> None:
        if not isinstance(definition, WidgetDefinition):
            raise TypeError("widget registry accepts WidgetDefinition values only.")
        if definition.widget_id in self._definitions:
            raise ValueError(f"widget '{definition.widget_id}' is already registered.")
        self._definitions[definition.widget_id] = definition

    def get(self, widget_id: str) -> WidgetDefinition:
        try:
            return self._definitions[widget_id]
        except KeyError as error:
            raise WidgetPropsError(f"unknown widget_id '{widget_id}'.") from error

    def widget_ids(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def widget_index(
        self,
        allowed_widget_ids: tuple[str, ...] | None = None,
    ) -> tuple[dict[str, str], ...]:
        """Return the short discovery catalog safe for the Plan Agent's first turn."""

        allowed = set(allowed_widget_ids) if allowed_widget_ids is not None else None
        return tuple(
            {"id": definition.widget_id, "purpose": definition.purpose}
            for definition in self._definitions.values()
            if allowed is None or definition.widget_id in allowed
        )

def _validate_text(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"content", "role"}
    if unknown:
        raise WidgetPropsError(f"text.props has unsupported fields: {sorted(unknown)}.")
    content = _text(props.get("content"), "text.content")
    role = props.get("role", "body")
    if role not in {"title", "subtitle", "label", "body"}:
        raise WidgetPropsError("text.role must be title, subtitle, label or body.")
    return {"content": content, "role": role}


def _validate_image(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"asset_id", "label"}
    if unknown:
        raise WidgetPropsError(f"image.props has unsupported fields: {sorted(unknown)}.")
    normalized = {"asset_id": _text(props.get("asset_id"), "image.asset_id")}
    label = props.get("label")
    if label is not None:
        normalized["label"] = _text(label, "image.label")
    return normalized


def _validate_object_group(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"asset_id", "count", "label"}
    if unknown:
        raise WidgetPropsError(f"object_group.props has unsupported fields: {sorted(unknown)}.")
    normalized: dict[str, Any] = {
        "asset_id": _text(props.get("asset_id"), "object_group.asset_id"),
        "count": _positive_integer(props.get("count"), "object_group.count"),
    }
    label = props.get("label")
    if label is not None:
        normalized["label"] = _text(label, "object_group.label")
    return normalized


def _validate_answer(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"value"}
    if unknown:
        raise WidgetPropsError(f"answer.props has unsupported fields: {sorted(unknown)}.")
    value = _text(props.get("value"), "answer.value")
    if len(value) > 80:
        raise WidgetPropsError("answer.value must not exceed 80 characters.")
    return {"value": value}


def _validate_number_display(props: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(props) - {"value"}
    if unknown:
        raise WidgetPropsError(f"number_display.props has unsupported fields: {sorted(unknown)}.")
    value = _text(props.get("value"), "number_display.value")
    if len(value) > 20:
        raise WidgetPropsError("number_display.value must not exceed 20 characters.")
    return {"value": value}


def _validate_choice(props: Mapping[str, Any]) -> dict[str, Any]:
    if props:
        raise WidgetPropsError("choice.props must be empty; the compiler assigns its interaction anchor.")
    return {}


def _no_anchors(_: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
    return ()


def _text_anchors(_: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
    return (WidgetAnchor(key="text", allowed_effect_ids=("highlight", "circle")),)


def _image_anchors(_: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
    return (WidgetAnchor(key="image", allowed_effect_ids=("highlight", "circle")),)


def _object_group_anchors(props: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
    group = WidgetAnchor(key="group", allowed_effect_ids=("highlight", "circle"))
    items = tuple(
        WidgetAnchor(key=f"item_{index}", allowed_effect_ids=("highlight", "circle"))
        for index in range(1, props["count"] + 1)
    )
    return (group, *items)


def _answer_anchors(_: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
    return (WidgetAnchor(key="answer", allowed_effect_ids=("highlight", "circle")),)


def _number_display_anchors(_: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
    return (WidgetAnchor(key="number", allowed_effect_ids=("highlight", "circle")),)


def _choice_anchors(_: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
    return (WidgetAnchor(key="choice", allowed_effect_ids=("highlight", "circle")),)


def build_default_widget_registry() -> WidgetRegistry:
    """Return the initial registry without coupling it to any domain manifest."""

    visibility_state = WidgetStateDefinition(
        name="visibility",
        value_type="string",
        allowed_values=("visible", "hidden"),
        transitions={"visible": ("hidden",), "hidden": ("visible",)},
    )
    return WidgetRegistry(
        (
            WidgetDefinition(
                widget_id="text",
                validate_props=_validate_text,
                anchor_policy=_text_anchors,
                purpose="Hiển thị văn bản tự do như tiêu đề, nhãn hoặc nội dung ngắn.",
                props=(
                    WidgetPropDefinition(
                        name="content",
                        value_type="string",
                        required=True,
                        template_value_kind="binding",
                        description="Nội dung văn bản cần hiển thị.",
                    ),
                    WidgetPropDefinition(
                        name="role",
                        value_type="string",
                        required=False,
                        template_value_kind="structural",
                        description="Vai trò trình bày của văn bản.",
                        allowed_values=("title", "subtitle", "label", "body"),
                    ),
                ),
                state_fields=(visibility_state,),
            ),
            WidgetDefinition(
                widget_id="image",
                validate_props=_validate_image,
                anchor_policy=_image_anchors,
                purpose="Hiển thị một ảnh hoặc minh hoạ từ Asset Catalog.",
                props=(
                    WidgetPropDefinition(
                        name="asset_id",
                        value_type="string",
                        required=True,
                        template_value_kind="binding",
                        description="ID của asset ảnh sẽ hiển thị.",
                        source="asset_catalog.id",
                    ),
                    WidgetPropDefinition(
                        name="label",
                        value_type="string",
                        required=False,
                        template_value_kind="binding",
                        description="Nhãn ngắn cho ảnh.",
                    ),
                ),
                state_fields=(visibility_state,),
            ),
            WidgetDefinition(
                widget_id="object_group",
                validate_props=_validate_object_group,
                anchor_policy=_object_group_anchors,
                purpose="Hiển thị một nhóm nhiều bản sao của cùng asset.",
                props=(
                    WidgetPropDefinition(
                        name="asset_id",
                        value_type="string",
                        required=True,
                        template_value_kind="binding",
                        description="ID của asset được lặp trong nhóm.",
                        source="asset_catalog.id",
                    ),
                    WidgetPropDefinition(
                        name="count",
                        value_type="integer",
                        required=True,
                        template_value_kind="binding",
                        description="Số lượng bản sao của asset trong nhóm.",
                        minimum=1,
                    ),
                    WidgetPropDefinition(
                        name="label",
                        value_type="string",
                        required=False,
                        template_value_kind="binding",
                        description="Nhãn ngắn cho cả nhóm.",
                    ),
                ),
                state_fields=(visibility_state,),
            ),
            WidgetDefinition(
                widget_id="answer",
                validate_props=_validate_answer,
                anchor_policy=_answer_anchors,
                purpose=(
                    "Hiển thị một đáp án số hoặc chữ ngắn; khi hidden hiển thị dấu ?, "
                    "khi visible hiển thị value."
                ),
                props=(
                    WidgetPropDefinition(
                        name="value",
                        value_type="string",
                        required=True,
                        template_value_kind="binding",
                        description="Đáp án hoặc từ ngắn cần hiển thị.",
                    ),
                ),
                state_fields=(visibility_state,),
            ),
            WidgetDefinition(
                widget_id="number_display",
                validate_props=_validate_number_display,
                anchor_policy=_number_display_anchors,
                purpose="Hiển thị một số hoặc giá trị ngắn thật lớn, rõ ràng và cân giữa trong vùng toán học.",
                props=(
                    WidgetPropDefinition(
                        name="value",
                        value_type="string",
                        required=True,
                        template_value_kind="binding",
                        description="Số hoặc giá trị ngắn cần hiển thị nổi bật.",
                    ),
                ),
                state_fields=(visibility_state,),
            ),
            WidgetDefinition(
                widget_id="choice",
                validate_props=_validate_choice,
                anchor_policy=_choice_anchors,
                purpose="Tạo một lựa chọn có thể chạm/chọn; hiển thị ảnh hoặc nhóm ở trên và nhãn/chữ ở dưới.",
                props=(),
                state_fields=(visibility_state,),
                allowed_child_widget_ids=("image", "text", "number_display", "object_group"),
                interaction_event="select",
            ),
        )
    )
