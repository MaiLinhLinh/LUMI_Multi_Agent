"""Reusable layout templates extracted from concrete presentation plans.

A layout template preserves geometry and structural widget props, while values
that vary between uses are replaced with deterministic binding keys. The
materializer turns one stored frame plus concrete bindings back into the same
``PresentationPlan`` contract accepted by ``PanelCompiler``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from gemini_live_2.panel.contracts import (
    ChoiceChild,
    ContractValidationError,
    PlanBlock,
    PresentationPlan,
)
from gemini_live_2.widgets import WidgetPropsError, WidgetRegistry


class LayoutTemplateError(ValueError):
    """Raised when a reusable layout template cannot be safely represented."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LayoutTemplateError(f"{field_name} must be a non-empty string.")
    return value.strip()


@dataclass(frozen=True, slots=True)
class TemplateComponentContract:
    """One structural widget slot retained by a reusable template.

    This is deliberately a *shape* contract, not a copy of the data used by
    one surface.  It lets the Plan Agent distinguish, for example, an image
    comparison from a selectable choice card even if their grids look alike.
    """

    widget_id: str
    child_widget_ids: tuple[str, ...] = ()
    initial_state: Mapping[str, Any] | None = None
    interactions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "widget_id", _text(self.widget_id, "template component.widget_id"))
        children = tuple(_text(item, "template component child.widget_id") for item in self.child_widget_ids)
        object.__setattr__(self, "child_widget_ids", children)
        interactions = tuple(_text(item, "template component interaction") for item in self.interactions)
        if len(interactions) != len(set(interactions)):
            raise LayoutTemplateError("template component interactions must be unique.")
        object.__setattr__(self, "interactions", interactions)
        if self.initial_state is not None:
            if not isinstance(self.initial_state, Mapping):
                raise LayoutTemplateError("template component.initial_state must be an object.")
            object.__setattr__(self, "initial_state", dict(self.initial_state))

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"widget_id": self.widget_id}
        if self.child_widget_ids:
            data["child_widget_ids"] = list(self.child_widget_ids)
        if self.initial_state:
            data["initial_state"] = dict(self.initial_state)
        if self.interactions:
            data["interactions"] = list(self.interactions)
        return data

    @classmethod
    def from_dict(cls, value: object) -> "TemplateComponentContract":
        if not isinstance(value, Mapping):
            raise LayoutTemplateError("template component contract must be an object.")
        children = value.get("child_widget_ids", [])
        interactions = value.get("interactions", [])
        if not isinstance(children, list) or not all(isinstance(item, str) for item in children):
            raise LayoutTemplateError("template component child_widget_ids must be an array of strings.")
        if not isinstance(interactions, list) or not all(isinstance(item, str) for item in interactions):
            raise LayoutTemplateError("template component interactions must be an array of strings.")
        initial_state = value.get("initial_state")
        if initial_state is not None and not isinstance(initial_state, Mapping):
            raise LayoutTemplateError("template component.initial_state must be an object.")
        return cls(
            widget_id=value.get("widget_id"),
            child_widget_ids=tuple(children),
            initial_state=initial_state,
            interactions=tuple(interactions),
        )


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """Semantic index for matching a template by mechanics, slots and contract."""

    mechanics: tuple[str, ...]
    slots: tuple[str, ...]
    component_contracts: tuple[TemplateComponentContract, ...]

    def __post_init__(self) -> None:
        mechanics = tuple(_text(item, "template spec mechanic") for item in self.mechanics)
        if len(mechanics) != len(set(mechanics)):
            raise LayoutTemplateError("template spec mechanics must be unique.")
        slots = tuple(_text(item, "template spec slot") for item in self.slots)
        if len(slots) != len(set(slots)):
            raise LayoutTemplateError("template spec slots must be unique.")
        if not isinstance(self.component_contracts, tuple) or not all(
            isinstance(item, TemplateComponentContract) for item in self.component_contracts
        ):
            raise LayoutTemplateError("template spec component_contracts must contain TemplateComponentContract values.")
        object.__setattr__(self, "mechanics", mechanics)
        object.__setattr__(self, "slots", slots)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanics": list(self.mechanics),
            "slots": list(self.slots),
            "component_contracts": [item.to_dict() for item in self.component_contracts],
        }

    @classmethod
    def from_dict(cls, value: object) -> "TemplateSpec":
        if not isinstance(value, Mapping):
            raise LayoutTemplateError("template spec must be an object.")
        mechanics = value.get("mechanics", [])
        slots = value.get("slots", [])
        contracts = value.get("component_contracts", [])
        if not isinstance(mechanics, list) or not all(isinstance(item, str) for item in mechanics):
            raise LayoutTemplateError("template spec mechanics must be an array of strings.")
        if not isinstance(slots, list) or not all(isinstance(item, str) for item in slots):
            raise LayoutTemplateError("template spec slots must be an array of strings.")
        if not isinstance(contracts, list):
            raise LayoutTemplateError("template spec component_contracts must be an array.")
        return cls(
            mechanics=tuple(mechanics),
            slots=tuple(slots),
            component_contracts=tuple(TemplateComponentContract.from_dict(item) for item in contracts),
        )


@dataclass(frozen=True, slots=True)
class TemplateBinding:
    """One deterministic content placeholder in a reusable layout template."""

    key: str
    block_index: int
    prop_name: str
    value_type: str
    required: bool
    description: str
    source: str | None = None
    child_index: int | None = None

    def __post_init__(self) -> None:
        key = _text(self.key, "template binding.key")
        if not key.startswith("$block_"):
            raise LayoutTemplateError("template binding.key must start with '$block_'.")
        if isinstance(self.block_index, bool) or not isinstance(self.block_index, int) or self.block_index < 1:
            raise LayoutTemplateError("template binding.block_index must be a positive integer.")
        if not isinstance(self.required, bool):
            raise LayoutTemplateError("template binding.required must be a boolean.")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "prop_name", _text(self.prop_name, "template binding.prop_name"))
        object.__setattr__(self, "value_type", _text(self.value_type, "template binding.value_type"))
        object.__setattr__(self, "description", _text(self.description, "template binding.description"))
        if self.source is not None:
            object.__setattr__(self, "source", _text(self.source, "template binding.source"))
        if self.child_index is not None:
            if isinstance(self.child_index, bool) or not isinstance(self.child_index, int) or self.child_index < 1:
                raise LayoutTemplateError("template binding.child_index must be a positive integer.")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "block_index": self.block_index,
            "prop_name": self.prop_name,
            "type": self.value_type,
            "required": self.required,
            "description": self.description,
        }
        if self.source is not None:
            data["source"] = self.source
        if self.child_index is not None:
            data["child_index"] = self.child_index
        return data

    @classmethod
    def from_dict(cls, value: object) -> "TemplateBinding":
        if not isinstance(value, Mapping):
            raise LayoutTemplateError("template binding must be an object.")
        return cls(
            key=value.get("key"),
            block_index=value.get("block_index"),
            prop_name=value.get("prop_name"),
            value_type=value.get("type"),
            required=value.get("required"),
            description=value.get("description"),
            source=value.get("source"),
            child_index=value.get("child_index"),
        )


@dataclass(frozen=True, slots=True)
class LayoutTemplate:
    """A domain-owned reusable frame with binding placeholders in its props."""

    template_id: str
    domain_id: str
    description: str
    blocks: tuple[PlanBlock, ...]
    bindings: tuple[TemplateBinding, ...]
    semantic_spec: TemplateSpec | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "template_id", _text(self.template_id, "layout_template.id"))
        object.__setattr__(self, "domain_id", _text(self.domain_id, "layout_template.domain_id"))
        object.__setattr__(self, "description", _text(self.description, "layout_template.description"))
        if not isinstance(self.blocks, tuple) or not self.blocks or not all(isinstance(block, PlanBlock) for block in self.blocks):
            raise LayoutTemplateError("layout_template.blocks must contain PlanBlock values.")
        if not isinstance(self.bindings, tuple) or not all(isinstance(binding, TemplateBinding) for binding in self.bindings):
            raise LayoutTemplateError("layout_template.bindings must contain TemplateBinding values.")
        keys = [binding.key for binding in self.bindings]
        if len(keys) != len(set(keys)):
            raise LayoutTemplateError("layout_template.bindings contains duplicate keys.")
        placeholder_keys = {
            prop_value
            for block in self.blocks
            for props in (block.props, *(child.props for child in block.children))
            for prop_value in props.values()
            if isinstance(prop_value, str) and prop_value.startswith("$block_")
        }
        if placeholder_keys != set(keys):
            raise LayoutTemplateError("layout_template bindings must exactly match block placeholders.")
        if self.semantic_spec is None:
            object.__setattr__(self, "semantic_spec", TemplateSpec(
                mechanics=(),
                slots=tuple(binding.key for binding in self.bindings),
                component_contracts=tuple(
                    TemplateComponentContract(
                        widget_id=block.widget_id,
                        child_widget_ids=tuple(child.widget_id for child in block.children),
                        initial_state=block.initial_state or {"visibility": block.initial_visibility},
                    )
                    for block in self.blocks
                ),
            ))
        elif not isinstance(self.semantic_spec, TemplateSpec):
            raise LayoutTemplateError("layout_template.semantic_spec must be a TemplateSpec.")
        if self.semantic_spec.slots != tuple(keys):
            raise LayoutTemplateError("template spec slots must exactly match template bindings.")
        expected_contracts = tuple(
            (block.widget_id, tuple(child.widget_id for child in block.children),
             dict(block.initial_state or {"visibility": block.initial_visibility}))
            for block in self.blocks
        )
        actual_contracts = tuple(
            (contract.widget_id, contract.child_widget_ids, dict(contract.initial_state or {}))
            for contract in self.semantic_spec.component_contracts
        )
        if actual_contracts != expected_contracts:
            raise LayoutTemplateError("template spec component_contracts must exactly match template block structure.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "domain_id": self.domain_id,
            "description": self.description,
            "blocks": [block.to_dict() for block in self.blocks],
            "bindings": [binding.to_dict() for binding in self.bindings],
            "semantic_spec": self.semantic_spec.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> "LayoutTemplate":
        if not isinstance(value, Mapping):
            raise LayoutTemplateError("layout_template must be an object.")
        raw_blocks = value.get("blocks")
        raw_bindings = value.get("bindings")
        if not isinstance(raw_blocks, list):
            raise LayoutTemplateError("layout_template.blocks must be an array.")
        if not isinstance(raw_bindings, list):
            raise LayoutTemplateError("layout_template.bindings must be an array.")
        try:
            blocks = tuple(PlanBlock.from_dict(item) for item in raw_blocks)
        except ContractValidationError as exc:
            raise LayoutTemplateError(str(exc)) from exc
        return cls(
            template_id=value.get("template_id"),
            domain_id=value.get("domain_id"),
            description=value.get("description"),
            blocks=blocks,
            bindings=tuple(TemplateBinding.from_dict(item) for item in raw_bindings),
            semantic_spec=(
                TemplateSpec.from_dict(value["semantic_spec"])
                if "semantic_spec" in value else None
            ),
        )


@dataclass(frozen=True, slots=True)
class LayoutTemplateMaterializer:
    """Bind one reusable layout frame into a concrete presentation plan.

    It deliberately does not validate domain assets, geometry, collisions, or
    visual anchors. The existing PanelCompiler remains the sole owner of those
    checks after this boundary has replaced all template placeholders.
    """

    def materialize(
        self,
        *,
        template: LayoutTemplate,
        bindings: Mapping[str, Any],
    ) -> PresentationPlan:
        if not isinstance(bindings, Mapping):
            raise LayoutTemplateError("template bindings must be an object.")

        expected = {binding.key for binding in template.bindings}
        required = {binding.key for binding in template.bindings if binding.required}
        actual = set(bindings)
        missing = sorted(required - actual)
        unexpected = sorted(actual - expected)
        if missing or unexpected:
            details: list[str] = []
            if missing:
                details.append(f"missing bindings: {missing}")
            if unexpected:
                details.append(f"unexpected bindings: {unexpected}")
            raise LayoutTemplateError(
                "template bindings must match exactly (" + "; ".join(details) + ")."
            )

        blocks: list[PlanBlock] = []
        for block in template.blocks:
            props: dict[str, Any] = {}
            for prop_name, prop_value in block.props.items():
                if isinstance(prop_value, str) and prop_value in expected:
                    if prop_value in bindings:
                        props[prop_name] = bindings[prop_value]
                else:
                    props[prop_name] = prop_value
            children = []
            for child in block.children:
                child_props: dict[str, Any] = {}
                for prop_name, prop_value in child.props.items():
                    if isinstance(prop_value, str) and prop_value in expected:
                        if prop_value in bindings:
                            child_props[prop_name] = bindings[prop_value]
                    else:
                        child_props[prop_name] = prop_value
                children.append(ChoiceChild(widget_id=child.widget_id, props=child_props))
            blocks.append(PlanBlock(
                widget_id=block.widget_id,
                grid=block.grid,
                props=props,
                initial_visibility=block.initial_visibility,
                initial_state=block.initial_state,
                children=tuple(children),
            ))

        return PresentationPlan(
            domain_id=template.domain_id,
            template_id=template.template_id,
            blocks=tuple(blocks),
        )


@dataclass(frozen=True, slots=True)
class TemplateExtractor:
    """Convert a concrete plan into a reusable layout template deterministically."""

    widget_registry: WidgetRegistry

    def extract(
        self,
        *,
        plan: PresentationPlan,
        template_id: str,
        description: str,
    ) -> LayoutTemplate:
        blocks: list[PlanBlock] = []
        bindings: list[TemplateBinding] = []
        component_contracts: list[TemplateComponentContract] = []
        mechanics: set[str] = set()

        for block_index, block in enumerate(plan.blocks, start=1):
            try:
                widget = self.widget_registry.get(block.widget_id)
                normalized_props = widget.validate(block.props)
            except WidgetPropsError as exc:
                raise LayoutTemplateError(str(exc)) from exc

            prop_definitions = {prop.name: prop for prop in widget.props}
            mechanics.update(interaction.action for interaction in widget.interactions)
            template_props: dict[str, Any] = {}
            for prop_name, prop_value in normalized_props.items():
                prop = prop_definitions[prop_name]
                if prop.template_value_kind == "structural":
                    template_props[prop_name] = prop_value
                    continue

                binding_key = f"$block_{block_index}_{prop_name}"
                template_props[prop_name] = binding_key
                bindings.append(TemplateBinding(
                    key=binding_key,
                    block_index=block_index,
                    prop_name=prop_name,
                    value_type=prop.value_type,
                    # Image sources are XOR-optional in the public widget
                    # contract, but a saved image template needs the source it
                    # captured. Other optional props (for example label) stay
                    # optional when materializing a template.
                    required=(prop.required or (
                        widget.widget_id == "image"
                        and prop.name in {"asset_id", "remote_image_result_id"}
                    )),
                    description=prop.description,
                    source=prop.source,
                ))
            template_children = []
            for child_index, child in enumerate(block.children, start=1):
                try:
                    child_widget = self.widget_registry.get(child.widget_id)
                    normalized_child_props = child_widget.validate(child.props)
                except WidgetPropsError as exc:
                    raise LayoutTemplateError(str(exc)) from exc
                child_definitions = {prop.name: prop for prop in child_widget.props}
                mechanics.update(interaction.action for interaction in child_widget.interactions)
                child_template_props: dict[str, Any] = {}
                for prop_name, prop_value in normalized_child_props.items():
                    prop = child_definitions[prop_name]
                    if prop.template_value_kind == "structural":
                        child_template_props[prop_name] = prop_value
                        continue
                    binding_key = f"$block_{block_index}_child_{child_index}_{prop_name}"
                    child_template_props[prop_name] = binding_key
                    bindings.append(TemplateBinding(
                        key=binding_key,
                        block_index=block_index,
                        child_index=child_index,
                        prop_name=prop_name,
                        value_type=prop.value_type,
                        required=(prop.required or (
                            child_widget.widget_id == "image"
                            and prop.name in {"asset_id", "remote_image_result_id"}
                        )),
                        description=prop.description,
                        source=prop.source,
                    ))
                template_children.append(ChoiceChild(widget_id=child.widget_id, props=child_template_props))
            blocks.append(PlanBlock(
                widget_id=block.widget_id,
                grid=block.grid,
                props=template_props,
                initial_visibility=block.initial_visibility,
                initial_state=block.initial_state,
                children=tuple(template_children),
            ))
            component_contracts.append(TemplateComponentContract(
                widget_id=widget.widget_id,
                child_widget_ids=tuple(child.widget_id for child in block.children),
                initial_state=block.initial_state or {"visibility": block.initial_visibility},
                interactions=tuple(interaction.action for interaction in widget.interactions),
            ))

        return LayoutTemplate(
            template_id=template_id,
            domain_id=plan.domain_id,
            description=description,
            blocks=tuple(blocks),
            bindings=tuple(bindings),
            semantic_spec=TemplateSpec(
                mechanics=tuple(sorted(mechanics)),
                slots=tuple(binding.key for binding in bindings),
                component_contracts=tuple(component_contracts),
            ),
        )
