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
    default_value: Any
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
        # Validate the default through the same public type/value contract. A
        # transition is about a later mutation, not initial construction.
        self.validate_value(self.default_value)

    def validate_value(self, value: Any) -> Any:
        """Validate one state value without evaluating a state transition."""

        if self.value_type == "string":
            if not isinstance(value, str):
                raise WidgetPropsError(f"state.{self.name} must be a string.")
            if self.allowed_values and value not in self.allowed_values:
                raise WidgetPropsError(
                    f"state.{self.name} must be one of {list(self.allowed_values)}."
                )
        elif self.value_type == "boolean":
            if not isinstance(value, bool):
                raise WidgetPropsError(f"state.{self.name} must be a boolean.")
        else:  # A future widget supplies a supported type in the same registry change.
            raise WidgetPropsError(f"unsupported state type '{self.value_type}'.")
        return value

    def validate_change(self, *, current_value: Any, next_value: Any) -> Any:
        self.validate_value(next_value)
        allowed_targets = self.transitions.get(str(current_value))
        if allowed_targets is not None and next_value not in allowed_targets:
            raise WidgetPropsError(
                f"state.{self.name} cannot transition from '{current_value}' to '{next_value}'."
            )
        return next_value


@dataclass(frozen=True, slots=True)
class WidgetInteractionDefinition:
    """One browser action the Runtime may accept for a widget type.

    The action is a capability, not an instruction to mutate state. Runtime
    will apply the widget's transition policy in SD7; it must never trust an
    arbitrary state value sent by the browser.
    """

    action: str
    description: str
    state_rule: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _text(self.action, "widget interaction action"))
        object.__setattr__(self, "description", _text(self.description, "widget interaction description"))
        if not isinstance(self.state_rule, Mapping):
            raise WidgetPropsError("widget interaction state_rule must be an object.")
        normalized: dict[str, dict[str, str]] = {}
        for field_name, rule in self.state_rule.items():
            name = _text(field_name, "widget interaction state_rule field")
            if not isinstance(rule, Mapping) or set(rule) != {"op"}:
                raise WidgetPropsError("each interaction state_rule must contain exactly 'op'.")
            operation = _text(rule.get("op"), "widget interaction state_rule op")
            if operation not in {"toggle"}:
                raise WidgetPropsError(f"unsupported interaction state_rule operation '{operation}'.")
            normalized[name] = {"op": operation}
        object.__setattr__(self, "state_rule", normalized)

    def state_changes_for(self, current_state: Mapping[str, Any]) -> dict[str, Any]:
        """Apply this interaction's declarative, widget-owned state rule.

        The Runtime invokes this generic interpreter after it has resolved a
        trusted component.  It never branches on a widget ID; a future action
        may add another declared operation here only when a real widget needs
        it.
        """

        if not isinstance(current_state, Mapping):
            raise WidgetPropsError("widget interaction current_state must be an object.")
        changes: dict[str, Any] = {}
        for field_name, rule in self.state_rule.items():
            value = current_state.get(field_name)
            if rule["op"] == "toggle":
                if not isinstance(value, bool):
                    raise WidgetPropsError(
                        f"state.{field_name} must be a boolean to use interaction operation 'toggle'."
                    )
                changes[field_name] = not value
        return changes


@dataclass(frozen=True, slots=True)
class WidgetAssetReferenceDefinition:
    """One asset path a widget renders and Compiler must validate."""

    path: str
    allowed_kinds: tuple[str, ...] = ("image",)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _text(self.path, "widget asset reference path"))
        kinds = tuple(_text(kind, "widget asset reference allowed kind") for kind in self.allowed_kinds)
        if not kinds or len(kinds) != len(set(kinds)):
            raise WidgetPropsError("widget asset reference allowed_kinds must be non-empty and unique.")
        object.__setattr__(self, "allowed_kinds", kinds)


@dataclass(frozen=True, slots=True)
class StageMapTextSource:
    """One text value genuinely rendered by a state-dependent map view."""

    content_label: str | None
    text_source: str
    quote_text: bool = False

    def __post_init__(self) -> None:
        if self.content_label is not None:
            object.__setattr__(self, "content_label", _text(self.content_label, "stage map text content_label"))
        object.__setattr__(self, "text_source", _text(self.text_source, "stage map text source"))
        if not isinstance(self.quote_text, bool):
            raise WidgetPropsError("stage map text quote_text must be a boolean.")


@dataclass(frozen=True, slots=True)
class StageMapView:
    """A policy view selected solely from one declared component state value."""

    state_field: str
    state_value: bool
    policy: "StageMapPolicy"

    def __post_init__(self) -> None:
        object.__setattr__(self, "state_field", _text(self.state_field, "stage map view state_field"))
        if not isinstance(self.state_value, bool):
            raise WidgetPropsError("stage map view state_value must be a boolean.")
        if not isinstance(self.policy, StageMapPolicy):
            raise WidgetPropsError("stage map view policy must be a StageMapPolicy.")


@dataclass(frozen=True, slots=True)
class StageMapPolicy:
    """Structured, renderer-facing description of visible widget semantics.

    It deliberately names data sources rather than generating prose. SD6's
    generic stage-map renderer will resolve these paths and use only fields the
    corresponding browser widget actually renders.
    """

    kind: str
    content_label: str | None = None
    quote_text: bool = False
    anchor_key: str | None = None
    text_source: str | None = None
    asset_source: str | None = None
    asset_text_source: str | None = None
    count_source: str | None = None
    item_anchor_prefix: str | None = None
    text_rendered: bool = True
    children_layout: str | None = None
    text_sources: tuple[StageMapTextSource, ...] = ()
    views: tuple[StageMapView, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _text(self.kind, "stage map kind"))
        for field_name in (
            "content_label", "anchor_key", "text_source", "asset_source", "asset_text_source",
            "count_source", "item_anchor_prefix", "children_layout",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _text(value, f"stage map {field_name}"))
        if not isinstance(self.text_rendered, bool):
            raise WidgetPropsError("stage map text_rendered must be a boolean.")
        if not isinstance(self.quote_text, bool):
            raise WidgetPropsError("stage map quote_text must be a boolean.")
        if not isinstance(self.text_sources, tuple) or not all(
            isinstance(item, StageMapTextSource) for item in self.text_sources
        ):
            raise WidgetPropsError("stage map text_sources must contain StageMapTextSource values.")
        if not isinstance(self.views, tuple) or not all(isinstance(item, StageMapView) for item in self.views):
            raise WidgetPropsError("stage map views must contain StageMapView values.")
        view_keys = tuple((item.state_field, item.state_value) for item in self.views)
        if len(view_keys) != len(set(view_keys)):
            raise WidgetPropsError("stage map views must be unique per state value.")

    def to_public_contract(self) -> dict[str, Any]:
        contract: dict[str, Any] = {
            "kind": self.kind,
            "text_rendered": self.text_rendered,
            "quote_text": self.quote_text,
        }
        for field_name in (
            "content_label", "anchor_key", "text_source", "asset_source", "asset_text_source",
            "count_source", "item_anchor_prefix", "children_layout",
        ):
            value = getattr(self, field_name)
            if value is not None:
                contract[field_name] = value
        if self.text_sources:
            contract["text_sources"] = [
                {
                    "content_label": item.content_label,
                    "text_source": item.text_source,
                    "quote_text": item.quote_text,
                }
                for item in self.text_sources
            ]
        if self.views:
            contract["views"] = [
                {"state_field": item.state_field, "state_value": item.state_value}
                for item in self.views
            ]
        return contract

    def for_state(self, state: Mapping[str, Any]) -> "StageMapPolicy":
        for view in self.views:
            if state.get(view.state_field) == view.state_value:
                return view.policy
        return self


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
    interactions: tuple[WidgetInteractionDefinition, ...] = ()
    stage_map_policy: StageMapPolicy | None = None
    asset_references: tuple[WidgetAssetReferenceDefinition, ...] = ()

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
        if not isinstance(self.interactions, tuple) or not all(
            isinstance(item, WidgetInteractionDefinition) for item in self.interactions
        ):
            raise WidgetPropsError("widget interactions must contain WidgetInteractionDefinition values.")
        actions = tuple(item.action for item in self.interactions)
        if len(actions) != len(set(actions)):
            raise WidgetPropsError("widget interaction actions must be unique.")
        if self.stage_map_policy is not None and not isinstance(self.stage_map_policy, StageMapPolicy):
            raise WidgetPropsError("widget stage_map_policy must be a StageMapPolicy or None.")
        if not isinstance(self.asset_references, tuple) or not all(
            isinstance(item, WidgetAssetReferenceDefinition) for item in self.asset_references
        ):
            raise WidgetPropsError("widget asset_references must contain WidgetAssetReferenceDefinition values.")
        reference_paths = tuple(item.path for item in self.asset_references)
        if len(reference_paths) != len(set(reference_paths)):
            raise WidgetPropsError("widget asset reference paths must be unique.")

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
                    "default": state.default_value,
                    "allowed_values": list(state.allowed_values),
                    "transitions": {
                        source: list(targets) for source, targets in state.transitions.items()
                    },
                }
                for state in self.state_fields
            }
        if self.allowed_child_widget_ids:
            contract["allowed_child_widget_ids"] = list(self.allowed_child_widget_ids)
        if self.interactions:
            contract["interactions"] = [
                {
                    "action": item.action,
                    "description": item.description,
                    **({"state_rule": item.state_rule} if item.state_rule else {}),
                }
                for item in self.interactions
            ]
        if self.stage_map_policy is not None:
            contract["stage_map_policy"] = self.stage_map_policy.to_public_contract()
        if self.asset_references:
            contract["asset_references"] = [
                {"path": item.path, "allowed_kinds": list(item.allowed_kinds)}
                for item in self.asset_references
            ]
        return contract

    @property
    def default_state(self) -> dict[str, Any]:
        """The only state a new component receives before an interaction."""

        return {definition.name: definition.default_value for definition in self.state_fields}

    @property
    def interaction_event(self) -> str | None:
        """Compatibility view for pre-SD7 callers that support one action."""

        return self.interactions[0].action if len(self.interactions) == 1 else None

    def allows_interaction(self, action: str) -> bool:
        return any(item.action == action for item in self.interactions)

    def interaction_state_changes(
        self, *, action: str, current_state: Mapping[str, Any]
    ) -> dict[str, Any]:
        for interaction in self.interactions:
            if interaction.action == action:
                changes = interaction.state_changes_for(current_state)
                if not changes:
                    return {}
                self.validate_state_changes(current_state=current_state, changes=changes)
                return changes
        raise WidgetPropsError(f"{self.widget_id} does not allow interaction action '{action}'.")

    def materialize_initial_state(self, initial_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Merge a plan's initial state with registry defaults without transitions."""

        if initial_state is None:
            initial_state = {}
        if not isinstance(initial_state, Mapping):
            raise WidgetPropsError("initial state must be an object.")
        definitions = {field.name: field for field in self.state_fields}
        unsupported = set(initial_state) - set(definitions)
        if unsupported:
            raise WidgetPropsError(
                f"{self.widget_id} does not allow initial state fields: {sorted(unsupported)}."
            )
        state = self.default_state
        for field_name, value in initial_state.items():
            state[field_name] = definitions[field_name].validate_value(value)
        return state

    def validate_child_widget_ids(self, child_widget_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Validate a parent widget's declared child widget types.

        Child props are validated by the referenced child WidgetDefinition. The
        compiler will compose both checks in SD3; keeping this policy here
        prevents containers from accepting arbitrary nested widget types.
        """

        if not isinstance(child_widget_ids, tuple) or not all(isinstance(item, str) for item in child_widget_ids):
            raise WidgetPropsError("child widget ids must be a tuple of strings.")
        normalized = tuple(_text(item, "child widget_id") for item in child_widget_ids)
        if not normalized:
            return ()
        if not self.allowed_child_widget_ids:
            raise WidgetPropsError(f"{self.widget_id} does not allow child widgets.")
        unsupported = set(normalized) - set(self.allowed_child_widget_ids)
        if unsupported:
            raise WidgetPropsError(
                f"{self.widget_id} does not allow child widget ids: {sorted(unsupported)}."
            )
        return normalized

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
    ) -> tuple[dict[str, Any], ...]:
        """Return the short discovery catalog safe for the Plan Agent's first turn."""

        allowed = set(allowed_widget_ids) if allowed_widget_ids is not None else None
        index: list[dict[str, Any]] = []
        for definition in self._definitions.values():
            if allowed is not None and definition.widget_id not in allowed:
                continue
            item: dict[str, Any] = {"id": definition.widget_id, "purpose": definition.purpose}
            if definition.allowed_child_widget_ids:
                item["allows_children"] = True
            if definition.interactions:
                item["interaction_actions"] = [item.action for item in definition.interactions]
            index.append(item)
        return tuple(index)

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


def _validate_flashcard(props: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exactly the two faces that the browser flashcard renders."""

    if set(props) != {"front", "back"}:
        raise WidgetPropsError("flashcard.props must contain exactly front and back.")
    front = props.get("front")
    back = props.get("back")
    if not isinstance(front, Mapping) or set(front) != {"asset_id", "text"}:
        raise WidgetPropsError("flashcard.front must contain exactly asset_id and text.")
    if not isinstance(back, Mapping) or set(back) != {"word", "phonetic", "meaning"}:
        raise WidgetPropsError("flashcard.back must contain exactly word, phonetic and meaning.")
    normalized_front = {
        "asset_id": _text(front.get("asset_id"), "flashcard.front.asset_id"),
        "text": _text(front.get("text"), "flashcard.front.text"),
    }
    normalized_back = {
        "word": _text(back.get("word"), "flashcard.back.word"),
        "phonetic": _text(back.get("phonetic"), "flashcard.back.phonetic"),
        "meaning": _text(back.get("meaning"), "flashcard.back.meaning"),
    }
    for path, value in (
        ("flashcard.front.text", normalized_front["text"]),
        ("flashcard.back.word", normalized_back["word"]),
        ("flashcard.back.phonetic", normalized_back["phonetic"]),
        ("flashcard.back.meaning", normalized_back["meaning"]),
    ):
        if len(value) > 80:
            raise WidgetPropsError(f"{path} must not exceed 80 characters.")
    return {"front": normalized_front, "back": normalized_back}


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


def _flashcard_anchors(_: Mapping[str, Any]) -> tuple[WidgetAnchor, ...]:
    return (WidgetAnchor(key="card", allowed_effect_ids=("highlight", "circle")),)


def build_default_widget_registry() -> WidgetRegistry:
    """Return the initial registry without coupling it to any domain manifest."""

    visibility_state = WidgetStateDefinition(
        name="visibility",
        value_type="string",
        default_value="visible",
        allowed_values=("visible", "hidden"),
        transitions={"visible": ("hidden",), "hidden": ("visible",)},
    )
    selected_state = WidgetStateDefinition(
        name="selected",
        value_type="boolean",
        default_value=False,
    )
    flipped_state = WidgetStateDefinition(
        name="flipped",
        value_type="boolean",
        default_value=False,
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
                stage_map_policy=StageMapPolicy(
                    kind="text",
                    content_label="CHỮ",
                    quote_text=True,
                    text_source="props.content",
                    anchor_key="text",
                ),
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
                stage_map_policy=StageMapPolicy(
                    kind="image",
                    content_label="ẢNH",
                    asset_source="props.asset_id",
                    asset_text_source="asset.caption",
                    anchor_key="image",
                    text_rendered=False,
                ),
                asset_references=(WidgetAssetReferenceDefinition(
                    path="props.asset_id", allowed_kinds=("image", "icon"),
                ),),
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
                stage_map_policy=StageMapPolicy(
                    kind="object_group",
                    content_label="NHÓM",
                    asset_source="props.asset_id",
                    asset_text_source="asset.caption",
                    count_source="props.count",
                    anchor_key="group",
                    item_anchor_prefix="item_",
                    text_rendered=False,
                ),
                asset_references=(WidgetAssetReferenceDefinition(path="props.asset_id"),),
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
                stage_map_policy=StageMapPolicy(
                    kind="answer",
                    content_label="KẾT QUẢ",
                    text_source="props.value",
                    anchor_key="answer",
                ),
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
                stage_map_policy=StageMapPolicy(
                    kind="number_display",
                    content_label="SỐ",
                    text_source="props.value",
                    anchor_key="number",
                ),
            ),
            WidgetDefinition(
                widget_id="choice",
                validate_props=_validate_choice,
                anchor_policy=_choice_anchors,
                purpose="Tạo một lựa chọn có thể chạm/chọn; hiển thị ảnh hoặc nhóm ở trên và nhãn/chữ ở dưới.",
                props=(),
                state_fields=(visibility_state, selected_state),
                allowed_child_widget_ids=("image", "text", "number_display", "object_group"),
                interactions=(WidgetInteractionDefinition(
                    action="select", description="Trẻ chạm hoặc chọn toàn bộ thẻ lựa chọn.",
                ),),
                stage_map_policy=StageMapPolicy(
                    kind="container", anchor_key="choice", children_layout="vertical",
                ),
            ),
            WidgetDefinition(
                widget_id="flashcard",
                validate_props=_validate_flashcard,
                anchor_policy=_flashcard_anchors,
                purpose="Hiển thị thẻ từ vựng có thể lật giữa mặt ảnh và mặt kiến thức.",
                props=(
                    WidgetPropDefinition(
                        name="front",
                        value_type="object",
                        required=True,
                        template_value_kind="binding",
                        description="Mặt trước gồm asset_id ảnh và chữ ngắn thật sự hiển thị.",
                    ),
                    WidgetPropDefinition(
                        name="back",
                        value_type="object",
                        required=True,
                        template_value_kind="binding",
                        description="Mặt sau gồm word, phonetic và meaning đều hiển thị.",
                    ),
                ),
                state_fields=(visibility_state, flipped_state),
                interactions=(WidgetInteractionDefinition(
                    action="flip",
                    description="Chạm hoặc dùng bàn phím để lật giữa hai mặt thẻ.",
                    state_rule={"flipped": {"op": "toggle"}},
                ),),
                stage_map_policy=StageMapPolicy(
                    kind="flashcard",
                    anchor_key="card",
                    views=(
                        StageMapView(
                            state_field="flipped",
                            state_value=False,
                            policy=StageMapPolicy(
                                kind="flashcard_front",
                                content_label="ẢNH",
                                asset_source="props.front.asset_id",
                                asset_text_source="asset.caption",
                                text_rendered=True,
                                text_sources=(StageMapTextSource(
                                    content_label="CHỮ",
                                    text_source="props.front.text",
                                    quote_text=True,
                                ),),
                            ),
                        ),
                        StageMapView(
                            state_field="flipped",
                            state_value=True,
                            policy=StageMapPolicy(
                                kind="flashcard_back",
                                text_rendered=True,
                                text_sources=(
                                    StageMapTextSource("TỪ", "props.back.word", True),
                                    StageMapTextSource("PHIÊN ÂM", "props.back.phonetic", True),
                                    StageMapTextSource("NGHĨA", "props.back.meaning", True),
                                ),
                            ),
                        ),
                    ),
                ),
                asset_references=(WidgetAssetReferenceDefinition(path="props.front.asset_id"),),
            ),
        )
    )
