"""Stable data contracts shared by routing, planning, compilation and presentation.

These types deliberately know nothing about a specific domain, widget renderer,
database, or Gemini API.  Later checkpoints attach those responsibilities to
the contracts rather than changing their meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, TypeAlias


JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
_VISIBILITY_STATES = frozenset({"visible", "hidden"})
_PATCH_OPERATION_NAMES = frozenset(
    {"add_block", "remove_block", "replace_block", "move_block", "update_props", "replace_children"}
)


class ContractValidationError(ValueError):
    """Raised when an external JSON value cannot become a framework contract."""


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{field_name} must be an object.")
    return value


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """The only semantic request Gemini Live will later send to the framework."""

    domain_id: str
    intent: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _required_text(self.domain_id, "domain_id"))
        object.__setattr__(self, "intent", _required_text(self.intent, "intent"))

    @classmethod
    def from_dict(cls, value: object) -> "RouteRequest":
        data = _mapping(value, "route request")
        return cls(domain_id=data.get("domain_id"), intent=data.get("intent"))

    def to_dict(self) -> dict[str, str]:
        return {"domain_id": self.domain_id, "intent": self.intent}


@dataclass(frozen=True, slots=True)
class GridRect:
    """One-based grid coordinates. Canvas-boundary checks belong to CP5."""

    col: int
    row: int
    col_span: int
    row_span: int

    def __post_init__(self) -> None:
        for field_name in ("col", "row", "col_span", "row_span"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ContractValidationError(f"grid.{field_name} must be a positive integer.")

    @classmethod
    def from_dict(cls, value: object) -> "GridRect":
        data = _mapping(value, "grid")
        return cls(
            col=data.get("col"),
            row=data.get("row"),
            col_span=data.get("col_span"),
            row_span=data.get("row_span"),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "col": self.col,
            "row": self.row,
            "col_span": self.col_span,
            "row_span": self.row_span,
        }


@dataclass(frozen=True, slots=True)
class ChoiceChild:
    """One widget rendered inside a choice block, without its own grid placement.

    Choice owns the outer grid cell.  Its renderer owns the initial vertical
    arrangement of these children, so a child is deliberately not a PlanBlock.
    """

    widget_id: str
    props: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "widget_id", _required_text(self.widget_id, "choice child.widget_id"))
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("choice child.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))

    @classmethod
    def from_dict(cls, value: object) -> "ChoiceChild":
        data = _mapping(value, "choice child")
        return cls(
            widget_id=data.get("widget_id"),
            props=_mapping(data.get("props", {}), "choice child.props"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"widget_id": self.widget_id, "props": dict(self.props)}


@dataclass(frozen=True, slots=True)
class PlanBlock:
    """A Plan Agent layout instruction without compiler-owned identifiers."""

    widget_id: str
    grid: GridRect
    props: Mapping[str, JsonValue] = field(default_factory=dict)
    initial_visibility: str = "visible"
    initial_state: Mapping[str, JsonValue] = field(default_factory=dict)
    children: tuple[ChoiceChild, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "widget_id", _required_text(self.widget_id, "block.widget_id"))
        if not isinstance(self.grid, GridRect):
            raise ContractValidationError("block.grid must be a GridRect.")
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("block.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))
        if self.initial_visibility not in _VISIBILITY_STATES:
            raise ContractValidationError("block.initial_visibility must be 'visible' or 'hidden'.")
        if not isinstance(self.initial_state, Mapping):
            raise ContractValidationError("block.initial_state must be an object.")
        normalized_initial_state = dict(self.initial_state)
        declared_visibility = normalized_initial_state.get("visibility")
        if declared_visibility is not None and declared_visibility != self.initial_visibility:
            raise ContractValidationError(
                "block.initial_state.visibility must match block.initial_visibility when both are provided."
            )
        object.__setattr__(self, "initial_state", normalized_initial_state)
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, ChoiceChild) for child in self.children
        ):
            raise ContractValidationError("block.children must contain ChoiceChild values.")

    @classmethod
    def from_dict(cls, value: object) -> "PlanBlock":
        data = _mapping(value, "block")
        props = data.get("props", {})
        children = data.get("children", [])
        initial_state = _mapping(data.get("initial_state", {}), "block.initial_state")
        if not isinstance(children, list):
            raise ContractValidationError("block.children must be an array.")
        initial_visibility = data.get("initial_visibility", initial_state.get("visibility", "visible"))
        return cls(
            widget_id=data.get("widget_id"),
            grid=GridRect.from_dict(data.get("grid")),
            props=_mapping(props, "block.props"),
            initial_visibility=initial_visibility,
            initial_state=initial_state,
            children=tuple(ChoiceChild.from_dict(item) for item in children),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "widget_id": self.widget_id,
            "grid": self.grid.to_dict(),
            "props": dict(self.props),
            "initial_visibility": self.initial_visibility,
        }
        if self.initial_state:
            data["initial_state"] = dict(self.initial_state)
        if self.children:
            data["children"] = [child.to_dict() for child in self.children]
        return data


@dataclass(frozen=True, slots=True)
class PresentationPlan:
    """Domain-neutral plan before trusted data is materialized into a panel."""

    domain_id: str
    blocks: tuple[PlanBlock, ...]
    template_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _required_text(self.domain_id, "plan.domain_id"))
        if self.template_id is not None:
            object.__setattr__(self, "template_id", _required_text(self.template_id, "plan.template_id"))
        if not isinstance(self.blocks, tuple) or not self.blocks:
            raise ContractValidationError("plan.blocks must contain at least one block.")
        if not all(isinstance(block, PlanBlock) for block in self.blocks):
            raise ContractValidationError("plan.blocks must contain PlanBlock values.")

    @classmethod
    def from_dict(cls, value: object) -> "PresentationPlan":
        data = _mapping(value, "presentation plan")
        blocks = data.get("blocks")
        if not isinstance(blocks, list):
            raise ContractValidationError("plan.blocks must be an array.")
        return cls(
            domain_id=data.get("domain_id"),
            template_id=data.get("template_id"),
            blocks=tuple(PlanBlock.from_dict(item) for item in blocks),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "domain_id": self.domain_id,
            "blocks": [block.to_dict() for block in self.blocks],
        }
        if self.template_id is not None:
            data["template_id"] = self.template_id
        return data


@dataclass(frozen=True, slots=True)
class CreateSurfacePlan:
    """Plan-Agent command to create one entirely new surface.

    The route has already established the domain, so this contract deliberately
    contains only the surface structure.  Runtime attaches the trusted domain
    and creates compiler-owned IDs, anchors and the first revision later.
    """

    blocks: tuple[PlanBlock, ...]
    template_description: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.blocks, tuple) or not self.blocks:
            raise ContractValidationError("create surface.blocks must contain at least one block.")
        if not all(isinstance(block, PlanBlock) for block in self.blocks):
            raise ContractValidationError("create surface.blocks must contain PlanBlock values.")
        if self.template_description is not None:
            object.__setattr__(
                self,
                "template_description",
                _required_text(self.template_description, "create surface.template_description"),
            )

    @classmethod
    def from_dict(cls, value: object) -> "CreateSurfacePlan":
        data = _mapping(value, "create surface plan")
        if data.get("action") != "create_surface_plan":
            raise ContractValidationError("create surface plan.action must be 'create_surface_plan'.")
        surface = _mapping(data.get("surface"), "create surface plan.surface")
        blocks = surface.get("blocks")
        if not isinstance(blocks, list):
            raise ContractValidationError("create surface plan.surface.blocks must be an array.")
        if "template_description" not in data:
            raise ContractValidationError("create surface plan.template_description is required.")
        return cls(
            blocks=tuple(PlanBlock.from_dict(block) for block in blocks),
            template_description=data.get("template_description"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action": "create_surface_plan",
            "surface": {"blocks": [block.to_dict() for block in self.blocks]},
        }
        if self.template_description is not None:
            result["template_description"] = self.template_description
        return result


@dataclass(frozen=True, slots=True)
class UseExistingSurfaceTemplate:
    """Instantiate one trusted reusable layout with only its variable bindings."""

    template_id: str
    bindings: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "template_id", _required_text(self.template_id, "template_id"))
        if not isinstance(self.bindings, Mapping):
            raise ContractValidationError("use existing template.bindings must be an object.")
        normalized: dict[str, JsonValue] = {}
        for key, value in self.bindings.items():
            binding_key = _required_text(key, "use existing template.bindings key")
            if not binding_key.startswith("$block_"):
                raise ContractValidationError("use existing template binding keys must start with '$block_'.")
            normalized[binding_key] = value
        object.__setattr__(self, "bindings", normalized)

    @classmethod
    def from_dict(cls, value: object) -> "UseExistingSurfaceTemplate":
        data = _mapping(value, "use existing surface template")
        if data.get("action") != "use_existing_surface_template":
            raise ContractValidationError(
                "use existing surface template.action must be 'use_existing_surface_template'."
            )
        return cls(
            template_id=data.get("template_id"),
            bindings=_mapping(data.get("bindings"), "use existing template.bindings"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": "use_existing_surface_template",
            "template_id": self.template_id,
            "bindings": dict(self.bindings),
        }


@dataclass(frozen=True, slots=True)
class AddBlockOperation:
    """Add one new block. Its stable IDs and anchors are Runtime-owned."""

    block: PlanBlock

    def __post_init__(self) -> None:
        if not isinstance(self.block, PlanBlock):
            raise ContractValidationError("add_block.block must be a PlanBlock.")

    def to_dict(self) -> dict[str, Any]:
        return {"op": "add_block", "block": self.block.to_dict()}


@dataclass(frozen=True, slots=True)
class RemoveBlockOperation:
    """Remove the existing component addressed through any one of its anchors."""

    anchor_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_id", _required_text(self.anchor_id, "remove_block.anchor_id"))

    def to_dict(self) -> dict[str, str]:
        return {"op": "remove_block", "anchor_id": self.anchor_id}


@dataclass(frozen=True, slots=True)
class ReplaceBlockOperation:
    """Replace one existing component with a freshly planned block."""

    anchor_id: str
    block: PlanBlock

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_id", _required_text(self.anchor_id, "replace_block.anchor_id"))
        if not isinstance(self.block, PlanBlock):
            raise ContractValidationError("replace_block.block must be a PlanBlock.")

    def to_dict(self) -> dict[str, Any]:
        return {"op": "replace_block", "anchor_id": self.anchor_id, "block": self.block.to_dict()}


@dataclass(frozen=True, slots=True)
class MoveBlockOperation:
    """Move or resize one component without changing its widget or props."""

    anchor_id: str
    grid: GridRect

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_id", _required_text(self.anchor_id, "move_block.anchor_id"))
        if not isinstance(self.grid, GridRect):
            raise ContractValidationError("move_block.grid must be a GridRect.")

    def to_dict(self) -> dict[str, Any]:
        return {"op": "move_block", "anchor_id": self.anchor_id, "grid": self.grid.to_dict()}


@dataclass(frozen=True, slots=True)
class UpdatePropsOperation:
    """Merge supplied prop changes into the addressed component's existing props.

    This operation never replaces the whole props object. Runtime later merges
    ``changes`` and validates the resulting complete widget props contract.
    """

    anchor_id: str
    changes: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_id", _required_text(self.anchor_id, "update_props.anchor_id"))
        if not isinstance(self.changes, Mapping) or not self.changes:
            raise ContractValidationError("update_props.changes must be a non-empty object.")
        normalized: dict[str, JsonValue] = {}
        for key, item in self.changes.items():
            normalized[_required_text(key, "update_props.changes key")] = item
        object.__setattr__(self, "changes", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {"op": "update_props", "anchor_id": self.anchor_id, "changes": dict(self.changes)}


@dataclass(frozen=True, slots=True)
class ReplaceChildrenOperation:
    """Replace all renderer-owned child widgets of one existing component.

    The parent component retains its runtime identity, grid, anchors and state.
    Runtime recompiles the supplied child declarations, so parent child-policy
    and every child widget's props remain validated before rendering.
    """

    anchor_id: str
    children: tuple[ChoiceChild, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_id", _required_text(self.anchor_id, "replace_children.anchor_id"))
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, ChoiceChild) for child in self.children
        ):
            raise ContractValidationError("replace_children.children must contain ChoiceChild values.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "replace_children",
            "anchor_id": self.anchor_id,
            "children": [child.to_dict() for child in self.children],
        }


PatchOperation: TypeAlias = (
    AddBlockOperation
    | RemoveBlockOperation
    | ReplaceBlockOperation
    | MoveBlockOperation
    | UpdatePropsOperation
    | ReplaceChildrenOperation
)


def _patch_operation_from_dict(value: object) -> PatchOperation:
    data = _mapping(value, "patch operation")
    operation = data.get("op")
    if operation not in _PATCH_OPERATION_NAMES:
        raise ContractValidationError("patch operation.op is not supported.")
    if operation == "add_block":
        return AddBlockOperation(block=PlanBlock.from_dict(data.get("block")))
    if operation == "remove_block":
        return RemoveBlockOperation(anchor_id=data.get("anchor_id"))
    if operation == "replace_block":
        return ReplaceBlockOperation(
            anchor_id=data.get("anchor_id"), block=PlanBlock.from_dict(data.get("block"))
        )
    if operation == "move_block":
        return MoveBlockOperation(anchor_id=data.get("anchor_id"), grid=GridRect.from_dict(data.get("grid")))
    if operation == "update_props":
        return UpdatePropsOperation(
            anchor_id=data.get("anchor_id"),
            changes=_mapping(data.get("changes"), "update_props.changes"),
        )
    children = data.get("children")
    if not isinstance(children, list):
        raise ContractValidationError("replace_children.children must be an array.")
    return ReplaceChildrenOperation(
        anchor_id=data.get("anchor_id"),
        children=tuple(ChoiceChild.from_dict(child) for child in children),
    )


@dataclass(frozen=True, slots=True)
class PatchSurfacePlan:
    """Plan-Agent command to change only the structure of the active surface."""

    surface_id: str
    base_revision: int
    operations: tuple[PatchOperation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface_id", _required_text(self.surface_id, "patch surface.surface_id"))
        if isinstance(self.base_revision, bool) or not isinstance(self.base_revision, int) or self.base_revision < 1:
            raise ContractValidationError("patch surface.base_revision must be a positive integer.")
        if not isinstance(self.operations, tuple) or not self.operations:
            raise ContractValidationError("patch surface.operations must not be empty.")
        if not all(isinstance(operation, (AddBlockOperation, RemoveBlockOperation, ReplaceBlockOperation,
                                          MoveBlockOperation, UpdatePropsOperation, ReplaceChildrenOperation)) for operation in self.operations):
            raise ContractValidationError("patch surface.operations contains an invalid operation.")

    @classmethod
    def from_dict(cls, value: object) -> "PatchSurfacePlan":
        data = _mapping(value, "patch surface plan")
        if data.get("action") != "patch_surface_plan":
            raise ContractValidationError("patch surface plan.action must be 'patch_surface_plan'.")
        operations = data.get("operations")
        if not isinstance(operations, list):
            raise ContractValidationError("patch surface plan.operations must be an array.")
        return cls(
            surface_id=data.get("surface_id"),
            base_revision=data.get("base_revision"),
            operations=tuple(_patch_operation_from_dict(operation) for operation in operations),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": "patch_surface_plan",
            "surface_id": self.surface_id,
            "base_revision": self.base_revision,
            "operations": [operation.to_dict() for operation in self.operations],
        }


@dataclass(frozen=True, slots=True)
class DeleteSurface:
    """Gemini-Live command to close the current surface at a known revision."""

    surface_id: str
    base_revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface_id", _required_text(self.surface_id, "delete surface.surface_id"))
        if isinstance(self.base_revision, bool) or not isinstance(self.base_revision, int) or self.base_revision < 1:
            raise ContractValidationError("delete surface.base_revision must be a positive integer.")

    @classmethod
    def from_dict(cls, value: object) -> "DeleteSurface":
        data = _mapping(value, "delete surface")
        return cls(surface_id=data.get("surface_id"), base_revision=data.get("base_revision"))

    def to_dict(self) -> dict[str, JsonValue]:
        return {"surface_id": self.surface_id, "base_revision": self.base_revision}


SurfacePlanCommand: TypeAlias = CreateSurfacePlan | PatchSurfacePlan | UseExistingSurfaceTemplate


def surface_plan_command_from_dict(value: object) -> SurfacePlanCommand:
    """Parse exactly one final Plan-Agent lifecycle decision."""

    data = _mapping(value, "surface plan command")
    action = data.get("action")
    if action == "create_surface_plan":
        return CreateSurfacePlan.from_dict(data)
    if action == "use_existing_surface_template":
        return UseExistingSurfaceTemplate.from_dict(data)
    if action == "patch_surface_plan":
        return PatchSurfacePlan.from_dict(data)
    raise ContractValidationError("surface plan command.action must create, reuse a template, or patch a surface.")


@dataclass(frozen=True, slots=True)
class ComponentChild:
    """A widget rendered by a parent component, without a panel-grid layout.

    Children are deliberately generic rather than choice-specific.  The Widget
    Registry decides which component types may contain them in a later
    checkpoint.  This contract only preserves their safe, renderer-facing
    widget type and props.
    """

    type: str
    props: Mapping[str, JsonValue] = field(default_factory=dict)
    children: tuple["ComponentChild", ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", _required_text(self.type, "component child.type"))
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("component child.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, ComponentChild) for child in self.children
        ):
            raise ContractValidationError("component child.children must contain ComponentChild values.")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type, "props": dict(self.props)}
        if self.children:
            data["children"] = [child.to_dict() for child in self.children]
        return data


@dataclass(frozen=True, slots=True)
class DataAlias:
    """A short, Plan-Agent-safe reference to a trusted value in a DataBundle."""

    id: str
    path: tuple[str, ...]
    description: str

    def __post_init__(self) -> None:
        alias = _required_text(self.id, "alias.id")
        if not alias.startswith("$") or len(alias) == 1:
            raise ContractValidationError("alias.id must start with '$'.")
        object.__setattr__(self, "id", alias)
        if not isinstance(self.path, tuple) or not self.path or not all(
            isinstance(part, str) and part for part in self.path
        ):
            raise ContractValidationError("alias.path must be a non-empty tuple of strings.")
        object.__setattr__(self, "description", _required_text(self.description, "alias.description"))

    @classmethod
    def from_dict(cls, value: object) -> "DataAlias":
        data = _mapping(value, "data alias")
        path = data.get("path")
        if not isinstance(path, list):
            raise ContractValidationError("alias.path must be an array.")
        return cls(id=data.get("id"), path=tuple(path), description=data.get("description"))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "path": list(self.path), "description": self.description}


@dataclass(frozen=True, slots=True)
class DataBundle:
    """Verified domain data plus the explicit aliases a plan may reference."""

    domain_id: str
    data: Mapping[str, JsonValue]
    aliases: tuple[DataAlias, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _required_text(self.domain_id, "bundle.domain_id"))
        if not isinstance(self.data, Mapping):
            raise ContractValidationError("bundle.data must be an object.")
        object.__setattr__(self, "data", dict(self.data))
        if not isinstance(self.aliases, tuple) or not all(isinstance(alias, DataAlias) for alias in self.aliases):
            raise ContractValidationError("bundle.aliases must contain DataAlias values.")
        alias_ids = [alias.id for alias in self.aliases]
        if len(alias_ids) != len(set(alias_ids)):
            raise ContractValidationError("bundle.aliases contains duplicate alias ids.")

    @property
    def alias_catalog(self) -> tuple[DataAlias, ...]:
        return self.aliases


@dataclass(frozen=True, slots=True)
class AnchorBinding:
    """Compiler-owned visual permission. Plan Agent never creates this directly."""

    anchor_id: str
    component_id: str
    anchor_key: str
    allowed_effect_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_id", _required_text(self.anchor_id, "anchor.anchor_id"))
        object.__setattr__(self, "component_id", _required_text(self.component_id, "anchor.component_id"))
        object.__setattr__(self, "anchor_key", _required_text(self.anchor_key, "anchor.anchor_key"))
        if not isinstance(self.allowed_effect_ids, tuple) or not self.allowed_effect_ids:
            raise ContractValidationError("anchor.allowed_effect_ids must not be empty.")
        effects = tuple(_required_text(effect, "anchor.allowed_effect_ids") for effect in self.allowed_effect_ids)
        if len(effects) != len(set(effects)):
            raise ContractValidationError("anchor.allowed_effect_ids contains duplicates.")
        object.__setattr__(self, "allowed_effect_ids", effects)

@dataclass(frozen=True, slots=True)
class ComponentNode:
    """One component on a generated surface's top-level CSS Grid.

    The component's ``state`` is intentionally an open mapping.  SD2 will
    make Widget Registry responsible for which keys and transitions are valid;
    this contract enforces only the universal visible/hidden baseline.
    """

    id: str
    type: str
    layout: GridRect
    props: Mapping[str, JsonValue] = field(default_factory=dict)
    state: Mapping[str, JsonValue] = field(default_factory=lambda: {"visibility": "visible"})
    children: tuple[ComponentChild, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "component.id"))
        object.__setattr__(self, "type", _required_text(self.type, "component.type"))
        if not isinstance(self.layout, GridRect):
            raise ContractValidationError("component.layout must be a GridRect.")
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("component.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))
        if not isinstance(self.state, Mapping):
            raise ContractValidationError("component.state must be an object.")
        normalized_state = dict(self.state)
        visibility = normalized_state.get("visibility")
        if visibility not in _VISIBILITY_STATES:
            raise ContractValidationError("component.state.visibility must be 'visible' or 'hidden'.")
        object.__setattr__(self, "state", normalized_state)
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, ComponentChild) for child in self.children
        ):
            raise ContractValidationError("component.children must contain ComponentChild values.")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "layout": self.layout.to_dict(),
            "props": dict(self.props),
            "state": dict(self.state),
        }
        if self.children:
            data["children"] = [child.to_dict() for child in self.children]
        return data


@dataclass(frozen=True, slots=True)
class SurfaceDocument:
    """The single authoritative structure and runtime state of a surface."""

    surface_id: str
    domain_id: str
    revision: int
    components: tuple[ComponentNode, ...]
    anchors: tuple[AnchorBinding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface_id", _required_text(self.surface_id, "surface document.surface_id"))
        object.__setattr__(self, "domain_id", _required_text(self.domain_id, "surface document.domain_id"))
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ContractValidationError("surface document.revision must be a positive integer.")
        if not isinstance(self.components, tuple) or not self.components:
            raise ContractValidationError("surface document.components must contain at least one ComponentNode.")
        if not all(isinstance(component, ComponentNode) for component in self.components):
            raise ContractValidationError("surface document.components must contain ComponentNode values.")
        if not isinstance(self.anchors, tuple) or not all(isinstance(anchor, AnchorBinding) for anchor in self.anchors):
            raise ContractValidationError("surface document.anchors must contain AnchorBinding values.")
        component_ids = [component.id for component in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ContractValidationError("surface document.components contains duplicate component ids.")
        anchor_ids = [anchor.anchor_id for anchor in self.anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ContractValidationError("surface document.anchors contains duplicate anchor ids.")
        if any(anchor.component_id not in set(component_ids) for anchor in self.anchors):
            raise ContractValidationError("surface document.anchor references an unknown component.")

    @property
    def component_map(self) -> dict[str, ComponentNode]:
        return {component.id: component for component in self.components}

    @property
    def anchor_map(self) -> dict[str, AnchorBinding]:
        return {anchor.anchor_id: anchor for anchor in self.anchors}

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "domain_id": self.domain_id,
            "revision": self.revision,
            "components": [component.to_dict() for component in self.components],
            "anchors": [
                {
                    "anchor_id": anchor.anchor_id,
                    "component_id": anchor.component_id,
                    "anchor_key": anchor.anchor_key,
                    "allowed_effect_ids": list(anchor.allowed_effect_ids),
                }
                for anchor in self.anchors
            ],
        }


@dataclass(frozen=True, slots=True)
class ActivePanelState:
    """Per-session active surface persisted as exactly one SurfaceDocument."""

    document: SurfaceDocument
    purpose: str

    def __post_init__(self) -> None:
        if not isinstance(self.document, SurfaceDocument):
            raise ContractValidationError("active panel requires a SurfaceDocument.")
        object.__setattr__(self, "purpose", _required_text(self.purpose, "active panel purpose"))

    @property
    def revision(self) -> int:
        return self.document.revision

    def replace(self, document: SurfaceDocument, *, purpose: str | None = None) -> "ActivePanelState":
        """Replace the surface while retaining purpose unless a new route supplies one."""

        return ActivePanelState(
            document=document,
            purpose=self.purpose if purpose is None else purpose,
        )


@dataclass(frozen=True, slots=True)
class ActiveSurfaceSummary:
    """Business-level context for a separate Plan Agent.

    It deliberately exposes neither browser targets nor CSS/Grid implementation
    details.  ``structure_summary`` is addressed through the public anchor IDs
    that the agent can use in a later patch command.
    """

    surface_id: str
    revision: int
    domain_id: str
    purpose: str
    structure_summary: tuple[Mapping[str, str], ...]
    state_summary: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface_id", _required_text(self.surface_id, "surface summary.surface_id"))
        object.__setattr__(self, "domain_id", _required_text(self.domain_id, "surface summary.domain_id"))
        object.__setattr__(self, "purpose", _required_text(self.purpose, "surface summary.purpose"))
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ContractValidationError("surface summary.revision must be a positive integer.")
        normalized_structure: list[dict[str, str]] = []
        for item in self.structure_summary:
            data = _mapping(item, "surface summary structure item")
            normalized_structure.append({
                "anchor_id": _required_text(data.get("anchor_id"), "surface summary.anchor_id"),
                "widget": _required_text(data.get("widget"), "surface summary.widget"),
                "description": _required_text(data.get("description"), "surface summary.description"),
            })
        object.__setattr__(self, "structure_summary", tuple(normalized_structure))
        if not isinstance(self.state_summary, Mapping):
            raise ContractValidationError("surface summary.state_summary must be an object.")
        object.__setattr__(self, "state_summary", dict(self.state_summary))

    @classmethod
    def from_active_panel(cls, active: ActivePanelState) -> "ActiveSurfaceSummary":
        anchors_by_component: dict[str, list[AnchorBinding]] = {}
        for anchor in active.document.anchors:
            anchors_by_component.setdefault(anchor.component_id, []).append(anchor)

        items: list[dict[str, str]] = []
        state_summary: dict[str, JsonValue] = {}
        for component in active.document.components:
            anchors = anchors_by_component.get(component.id, [])
            if not anchors:
                continue
            description = _component_description(component)
            for anchor in anchors:
                items.append({
                    "anchor_id": anchor.anchor_id,
                    "widget": component.type,
                    "description": description,
                })
            primary_anchor = anchors[0].anchor_id
            _append_nondefault_component_state(
                state_summary,
                anchor_id=primary_anchor,
                state=component.state,
            )
        return cls(
            surface_id=active.document.surface_id,
            revision=active.revision,
            domain_id=active.document.domain_id,
            purpose=active.purpose,
            structure_summary=tuple(items),
            state_summary=state_summary,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "revision": self.revision,
            "domain_id": self.domain_id,
            "purpose": self.purpose,
            "structure_summary": [dict(item) for item in self.structure_summary],
            "state_summary": dict(self.state_summary),
        }


def _component_description(component: ComponentNode) -> str:
    """Describe document content for the Plan Agent without layout internals."""

    if component.state.get("visibility") == "hidden":
        return "Nội dung đang ẩn"
    props = component.props
    if component.type == "text":
        return f'Nội dung chữ: "{str(props.get("content", ""))[:240]}"'
    if component.type == "image":
        label = props.get("label")
        return str(label)[:240] if isinstance(label, str) and label.strip() else f'Hình ảnh "{props.get("asset_id", "")}"'
    if component.type == "object_group":
        return f'Nhóm {props.get("count", 0)} × "{props.get("asset_id", "")}"'
    if component.type in {"answer", "number_display"}:
        return f'Giá trị "{props.get("value", "")}"'
    if component.type == "choice":
        child_labels = [
            str(child.props.get("label") or child.props.get("content") or child.props.get("asset_id") or "")
            for child in component.children
        ]
        visible = ", ".join(label for label in child_labels if label)[:240]
        return f"Lựa chọn: {visible}" if visible else "Lựa chọn tương tác"
    return component.type


def _append_nondefault_component_state(
    summary: dict[str, JsonValue], *, anchor_id: str, state: Mapping[str, JsonValue]
) -> None:
    """Keep non-default document state visible to a later structural planner."""

    for field_name, value in state.items():
        if field_name == "visibility" and value == "visible":
            continue
        if value is False or value is None:
            continue
        summary[f"{anchor_id}.{field_name}"] = value
