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
class TemplateBinding:
    """One deterministic content placeholder in a reusable layout template."""

    key: str
    block_index: int
    prop_name: str
    value_type: str
    required: bool
    description: str
    source: str | None = None

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
        )


@dataclass(frozen=True, slots=True)
class LayoutTemplate:
    """A domain-owned reusable frame with binding placeholders in its props."""

    template_id: str
    domain_id: str
    description: str
    blocks: tuple[PlanBlock, ...]
    bindings: tuple[TemplateBinding, ...]

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
            for prop_value in block.props.values()
            if isinstance(prop_value, str) and prop_value.startswith("$block_")
        }
        if placeholder_keys != set(keys):
            raise LayoutTemplateError("layout_template bindings must exactly match block placeholders.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "domain_id": self.domain_id,
            "description": self.description,
            "blocks": [block.to_dict() for block in self.blocks],
            "bindings": [binding.to_dict() for binding in self.bindings],
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
        actual = set(bindings)
        missing = sorted(expected - actual)
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
                    props[prop_name] = bindings[prop_value]
                else:
                    props[prop_name] = prop_value
            blocks.append(PlanBlock(widget_id=block.widget_id, grid=block.grid, props=props))

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

        for block_index, block in enumerate(plan.blocks, start=1):
            try:
                widget = self.widget_registry.get(block.widget_id)
                normalized_props = widget.validate(block.props)
            except WidgetPropsError as exc:
                raise LayoutTemplateError(str(exc)) from exc

            prop_definitions = {prop.name: prop for prop in widget.props}
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
                    required=prop.required,
                    description=prop.description,
                    source=prop.source,
                ))
            blocks.append(PlanBlock(widget_id=block.widget_id, grid=block.grid, props=template_props))

        return LayoutTemplate(
            template_id=template_id,
            domain_id=plan.domain_id,
            description=description,
            blocks=tuple(blocks),
            bindings=tuple(bindings),
        )
