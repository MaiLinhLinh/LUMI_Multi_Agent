"""Stable data contracts shared by routing, planning, compilation and presentation.

These types deliberately know nothing about a specific domain, widget renderer,
database, or Gemini API.  Later checkpoints attach those responsibilities to
the contracts rather than changing their meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, TypeAlias


JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
_VISIBILITY_STATES = frozenset({"visible", "hidden"})
_PATCH_OPERATION_NAMES = frozenset(
    {"add_block", "remove_block", "replace_block", "move_block", "update_props"}
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
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, ChoiceChild) for child in self.children
        ):
            raise ContractValidationError("block.children must contain ChoiceChild values.")

    @classmethod
    def from_dict(cls, value: object) -> "PlanBlock":
        data = _mapping(value, "block")
        props = data.get("props", {})
        children = data.get("children", [])
        if not isinstance(children, list):
            raise ContractValidationError("block.children must be an array.")
        return cls(
            widget_id=data.get("widget_id"),
            grid=GridRect.from_dict(data.get("grid")),
            props=_mapping(props, "block.props"),
            initial_visibility=data.get("initial_visibility", "visible"),
            children=tuple(ChoiceChild.from_dict(item) for item in children),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "widget_id": self.widget_id,
            "grid": self.grid.to_dict(),
            "props": dict(self.props),
            "initial_visibility": self.initial_visibility,
        }
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


PatchOperation: TypeAlias = (
    AddBlockOperation
    | RemoveBlockOperation
    | ReplaceBlockOperation
    | MoveBlockOperation
    | UpdatePropsOperation
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
    return UpdatePropsOperation(
        anchor_id=data.get("anchor_id"),
        changes=_mapping(data.get("changes"), "update_props.changes"),
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
                                          MoveBlockOperation, UpdatePropsOperation)) for operation in self.operations):
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
class PanelChoiceChild:
    """Trusted materialized child rendered inside a choice panel block."""

    widget_id: str
    props: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "widget_id", _required_text(self.widget_id, "panel choice child.widget_id"))
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("panel choice child.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))

    def to_dict(self) -> dict[str, Any]:
        return {"widget_id": self.widget_id, "props": dict(self.props)}


@dataclass(frozen=True, slots=True)
class PanelBlock:
    """Trusted materialized block with an identifier created by the Compiler."""

    id: str
    widget_id: str
    grid: GridRect
    props: Mapping[str, JsonValue] = field(default_factory=dict)
    visibility: str = "visible"
    children: tuple[PanelChoiceChild, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "panel block.id"))
        object.__setattr__(self, "widget_id", _required_text(self.widget_id, "panel block.widget_id"))
        if not isinstance(self.grid, GridRect):
            raise ContractValidationError("panel block.grid must be a GridRect.")
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("panel block.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))
        if self.visibility not in _VISIBILITY_STATES:
            raise ContractValidationError("panel block.visibility must be 'visible' or 'hidden'.")
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, PanelChoiceChild) for child in self.children
        ):
            raise ContractValidationError("panel block.children must contain PanelChoiceChild values.")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "widget_id": self.widget_id,
            "grid": self.grid.to_dict(),
            "props": dict(self.props),
            "visibility": self.visibility,
        }
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
    block_id: str
    anchor_key: str
    allowed_effect_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_id", _required_text(self.anchor_id, "anchor.anchor_id"))
        object.__setattr__(self, "block_id", _required_text(self.block_id, "anchor.block_id"))
        object.__setattr__(self, "anchor_key", _required_text(self.anchor_key, "anchor.anchor_key"))
        if not isinstance(self.allowed_effect_ids, tuple) or not self.allowed_effect_ids:
            raise ContractValidationError("anchor.allowed_effect_ids must not be empty.")
        effects = tuple(_required_text(effect, "anchor.allowed_effect_ids") for effect in self.allowed_effect_ids)
        if len(effects) != len(set(effects)):
            raise ContractValidationError("anchor.allowed_effect_ids contains duplicates.")
        object.__setattr__(self, "allowed_effect_ids", effects)


@dataclass(frozen=True, slots=True)
class SurfaceBlock:
    """One stable UI component without any mutable runtime state.

    Structure owns component identity, widget choice, layout, public props and
    nested children.  Visibility and other interaction values live in
    :class:`BlockState`, so Runtime can change state without rewriting the
    surface structure.
    """

    id: str
    widget_id: str
    grid: GridRect
    props: Mapping[str, JsonValue] = field(default_factory=dict)
    children: tuple[PanelChoiceChild, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "surface block.id"))
        object.__setattr__(self, "widget_id", _required_text(self.widget_id, "surface block.widget_id"))
        if not isinstance(self.grid, GridRect):
            raise ContractValidationError("surface block.grid must be a GridRect.")
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("surface block.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))
        if not isinstance(self.children, tuple) or not all(
            isinstance(child, PanelChoiceChild) for child in self.children
        ):
            raise ContractValidationError("surface block.children must contain PanelChoiceChild values.")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "widget_id": self.widget_id,
            "grid": self.grid.to_dict(),
            "props": dict(self.props),
        }
        if self.children:
            data["children"] = [child.to_dict() for child in self.children]
        return data


@dataclass(frozen=True, slots=True)
class BlockState:
    """Runtime values for one component.

    All six fields are intentionally present from SA1.  Widget Registry will
    later declare which fields and transitions each widget actually allows.
    """

    visibility: str = "visible"
    selected: bool = False
    flipped: bool = False
    position: Mapping[str, JsonValue] | None = None
    feedback: str | None = None
    progress: int | float | None = None

    def __post_init__(self) -> None:
        if self.visibility not in _VISIBILITY_STATES:
            raise ContractValidationError("block state.visibility must be 'visible' or 'hidden'.")
        for field_name in ("selected", "flipped"):
            if not isinstance(getattr(self, field_name), bool):
                raise ContractValidationError(f"block state.{field_name} must be a boolean.")
        if self.position is not None:
            if not isinstance(self.position, Mapping):
                raise ContractValidationError("block state.position must be an object or None.")
            object.__setattr__(self, "position", dict(self.position))
        if self.feedback is not None:
            object.__setattr__(self, "feedback", _required_text(self.feedback, "block state.feedback"))
        if self.progress is not None:
            if isinstance(self.progress, bool) or not isinstance(self.progress, (int, float)):
                raise ContractValidationError("block state.progress must be a number or None.")

    def to_dict(self) -> dict[str, JsonValue]:
        data: dict[str, JsonValue] = {
            "visibility": self.visibility,
            "selected": self.selected,
            "flipped": self.flipped,
        }
        if self.position is not None:
            data["position"] = dict(self.position)
        if self.feedback is not None:
            data["feedback"] = self.feedback
        if self.progress is not None:
            data["progress"] = self.progress
        return data


@dataclass(frozen=True, slots=True)
class SurfaceStructure:
    """Stable structure of one active surface, independent of runtime state."""

    surface_id: str
    domain_id: str
    blocks: tuple[SurfaceBlock, ...]
    anchors: tuple[AnchorBinding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "surface_id", _required_text(self.surface_id, "surface.surface_id"))
        object.__setattr__(self, "domain_id", _required_text(self.domain_id, "surface.domain_id"))
        if not isinstance(self.blocks, tuple) or not self.blocks:
            raise ContractValidationError("surface.blocks must contain at least one block.")
        if not all(isinstance(block, SurfaceBlock) for block in self.blocks):
            raise ContractValidationError("surface.blocks must contain SurfaceBlock values.")
        if not isinstance(self.anchors, tuple) or not all(isinstance(anchor, AnchorBinding) for anchor in self.anchors):
            raise ContractValidationError("surface.anchors must contain AnchorBinding values.")
        block_ids = [block.id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ContractValidationError("surface.blocks contains duplicate block ids.")
        anchor_ids = [anchor.anchor_id for anchor in self.anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ContractValidationError("surface.anchors contains duplicate anchor ids.")
        if any(anchor.block_id not in set(block_ids) for anchor in self.anchors):
            raise ContractValidationError("surface.anchor references an unknown block.")

    @classmethod
    def from_panel_ir(cls, panel: "PanelIR") -> "SurfaceStructure":
        return cls(
            surface_id=panel.panel_id,
            domain_id=panel.domain_id,
            blocks=tuple(
                SurfaceBlock(
                    id=block.id,
                    widget_id=block.widget_id,
                    grid=block.grid,
                    props=block.props,
                    children=block.children,
                )
                for block in panel.blocks
            ),
            anchors=panel.anchors,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "domain_id": self.domain_id,
            "blocks": [block.to_dict() for block in self.blocks],
            "anchors": [
                {
                    "anchor_id": anchor.anchor_id,
                    "block_id": anchor.block_id,
                    "anchor_key": anchor.anchor_key,
                    "allowed_effect_ids": list(anchor.allowed_effect_ids),
                }
                for anchor in self.anchors
            ],
        }


@dataclass(frozen=True, slots=True)
class SurfaceState:
    """All mutable per-block state of a surface, keyed by stable block ID."""

    block_states: Mapping[str, BlockState]

    def __post_init__(self) -> None:
        if not isinstance(self.block_states, Mapping):
            raise ContractValidationError("surface state.block_states must be an object.")
        normalized: dict[str, BlockState] = {}
        for block_id, state in self.block_states.items():
            normalized[_required_text(block_id, "surface state block id")] = state
            if not isinstance(state, BlockState):
                raise ContractValidationError("surface state.block_states must contain BlockState values.")
        object.__setattr__(self, "block_states", normalized)

    @classmethod
    def from_panel_ir(cls, panel: "PanelIR") -> "SurfaceState":
        return cls({block.id: BlockState(visibility=block.visibility) for block in panel.blocks})

    def state_for(self, block_id: str) -> BlockState:
        try:
            return self.block_states[block_id]
        except KeyError as error:
            raise ContractValidationError("surface state references an unknown block.") from error

    def to_dict(self) -> dict[str, dict[str, JsonValue]]:
        return {block_id: state.to_dict() for block_id, state in self.block_states.items()}

    def replace_block_states(self, replacements: Mapping[str, BlockState]) -> "SurfaceState":
        """Return a state value with an atomic set of per-block replacements."""

        if not isinstance(replacements, Mapping) or not replacements:
            raise ContractValidationError("surface state replacements must not be empty.")
        updated = dict(self.block_states)
        for block_id, block_state in replacements.items():
            normalized_id = _required_text(block_id, "surface state block id")
            if normalized_id not in updated:
                raise ContractValidationError("surface state replacement references an unknown block.")
            if not isinstance(block_state, BlockState):
                raise ContractValidationError("surface state replacements must contain BlockState values.")
            updated[normalized_id] = block_state
        return SurfaceState(updated)


def materialize_panel_ir(*, structure: SurfaceStructure, state: SurfaceState) -> "PanelIR":
    """Build the existing renderer contract from separated structure and state."""

    structure_ids = {block.id for block in structure.blocks}
    state_ids = set(state.block_states)
    if structure_ids != state_ids:
        raise ContractValidationError("surface structure and state must contain the same block ids.")
    return PanelIR(
        panel_id=structure.surface_id,
        domain_id=structure.domain_id,
        blocks=tuple(
            PanelBlock(
                id=block.id,
                widget_id=block.widget_id,
                grid=block.grid,
                props=block.props,
                visibility=state.state_for(block.id).visibility,
                children=block.children,
            )
            for block in structure.blocks
        ),
        anchors=structure.anchors,
    )


@dataclass(frozen=True, slots=True)
class PanelIR:
    """The sole source of truth for rendered UI, ASCII map and visual validation."""

    panel_id: str
    domain_id: str
    blocks: tuple[PanelBlock, ...]
    anchors: tuple[AnchorBinding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "panel_id", _required_text(self.panel_id, "panel.panel_id"))
        object.__setattr__(self, "domain_id", _required_text(self.domain_id, "panel.domain_id"))
        if not isinstance(self.blocks, tuple) or not self.blocks:
            raise ContractValidationError("panel.blocks must contain at least one block.")
        if not all(isinstance(block, PanelBlock) for block in self.blocks):
            raise ContractValidationError("panel.blocks must contain PanelBlock values.")
        if not isinstance(self.anchors, tuple) or not all(isinstance(anchor, AnchorBinding) for anchor in self.anchors):
            raise ContractValidationError("panel.anchors must contain AnchorBinding values.")
        anchor_ids = [anchor.anchor_id for anchor in self.anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ContractValidationError("panel.anchors contains duplicate anchor ids.")

    @property
    def anchor_map(self) -> dict[str, AnchorBinding]:
        return {anchor.anchor_id: anchor for anchor in self.anchors}

    @property
    def block_map(self) -> dict[str, PanelBlock]:
        return {block.id: block for block in self.blocks}

    def with_block_visibility(self, *, block_ids: set[str], visibility: str) -> "PanelIR":
        """Return this panel with only selected blocks changing visibility.

        The panel, block, anchor and target identities remain stable: reveal
        replaces the placeholder in place rather than creating another panel.
        """

        if visibility not in _VISIBILITY_STATES:
            raise ContractValidationError("panel visibility must be 'visible' or 'hidden'.")
        unknown_ids = block_ids.difference(self.block_map)
        if unknown_ids:
            raise ContractValidationError("panel update references an unknown block.")
        return PanelIR(
            panel_id=self.panel_id,
            domain_id=self.domain_id,
            blocks=tuple(
                replace(block, visibility=visibility) if block.id in block_ids else block
                for block in self.blocks
            ),
            anchors=self.anchors,
        )


@dataclass(frozen=True, slots=True)
class ActivePanelState:
    """Per-session active surface with separated structure and runtime state.

    ``panel_ir=...`` remains accepted while callers migrate.  It is converted
    immediately, so the persisted source of truth is still structure + state.
    """

    structure: SurfaceStructure
    state: SurfaceState
    purpose: str
    revision: int = 1

    def __init__(
        self,
        *,
        structure: SurfaceStructure | None = None,
        state: SurfaceState | None = None,
        panel_ir: PanelIR | None = None,
        purpose: str | None = None,
        revision: int = 1,
    ) -> None:
        if panel_ir is not None:
            if structure is not None or state is not None:
                raise ContractValidationError("active panel accepts either panel_ir or structure/state, not both.")
            structure = SurfaceStructure.from_panel_ir(panel_ir)
            state = SurfaceState.from_panel_ir(panel_ir)
        if not isinstance(structure, SurfaceStructure) or not isinstance(state, SurfaceState):
            raise ContractValidationError("active panel requires SurfaceStructure and SurfaceState.")
        purpose = _required_text(purpose, "active panel purpose")
        materialize_panel_ir(structure=structure, state=state)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ContractValidationError("active panel revision must be a positive integer.")
        object.__setattr__(self, "structure", structure)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "revision", revision)

    @property
    def panel_ir(self) -> PanelIR:
        """Compatibility view for current renderer and presentation callers."""

        return materialize_panel_ir(structure=self.structure, state=self.state)

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ContractValidationError("active panel revision must be a positive integer.")

    def replace(self, panel_ir: PanelIR, *, purpose: str | None = None) -> "ActivePanelState":
        """Replace the surface while retaining purpose unless a new route supplies one."""

        return ActivePanelState(
            panel_ir=panel_ir,
            purpose=self.purpose if purpose is None else purpose,
            revision=self.revision + 1,
        )

    def replace_state(self, state: SurfaceState) -> "ActivePanelState":
        """Persist a validated runtime-state change without altering structure."""

        return ActivePanelState(
            structure=self.structure,
            state=state,
            purpose=self.purpose,
            revision=self.revision + 1,
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
        anchors_by_block: dict[str, list[AnchorBinding]] = {}
        for anchor in active.structure.anchors:
            anchors_by_block.setdefault(anchor.block_id, []).append(anchor)

        items: list[dict[str, str]] = []
        state_summary: dict[str, JsonValue] = {}
        for block in active.structure.blocks:
            anchors = anchors_by_block.get(block.id, [])
            if not anchors:
                continue
            description = _surface_block_description(block, active.state.state_for(block.id))
            for anchor in anchors:
                items.append({
                    "anchor_id": anchor.anchor_id,
                    "widget": block.widget_id,
                    "description": description,
                })
            primary_anchor = anchors[0].anchor_id
            _append_nondefault_state(
                state_summary,
                anchor_id=primary_anchor,
                state=active.state.state_for(block.id),
            )
        return cls(
            surface_id=active.structure.surface_id,
            revision=active.revision,
            domain_id=active.structure.domain_id,
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


def _surface_block_description(block: SurfaceBlock, state: BlockState) -> str:
    """Describe public visible meaning without exposing rendering implementation."""

    if state.visibility == "hidden":
        return "Nội dung đang ẩn"
    props = block.props
    if block.widget_id == "text":
        return f'Nội dung chữ: "{str(props.get("content", ""))[:240]}"'
    if block.widget_id == "image":
        label = props.get("label")
        return str(label)[:240] if isinstance(label, str) and label.strip() else f'Hình ảnh "{props.get("asset_id", "")}"'
    if block.widget_id == "object_group":
        return f'Nhóm {props.get("count", 0)} × "{props.get("asset_id", "")}"'
    if block.widget_id in {"answer", "number_display"}:
        return f'Giá trị "{props.get("value", "")}"'
    if block.widget_id == "choice":
        child_labels = [
            str(child.props.get("label") or child.props.get("content") or child.props.get("asset_id") or "")
            for child in block.children
        ]
        visible = ", ".join(label for label in child_labels if label)[:240]
        return f"Lựa chọn: {visible}" if visible else "Lựa chọn tương tác"
    return block.widget_id


def _append_nondefault_state(
    summary: dict[str, JsonValue], *, anchor_id: str, state: BlockState
) -> None:
    """State absent from a summary means the widget's declared default value."""

    if state.visibility != "visible":
        summary[f"{anchor_id}.visibility"] = state.visibility
    if state.selected:
        summary[f"{anchor_id}.selected"] = True
    if state.flipped:
        summary[f"{anchor_id}.flipped"] = True
    if state.position is not None:
        summary[f"{anchor_id}.position"] = dict(state.position)
    if state.feedback is not None:
        summary[f"{anchor_id}.feedback"] = state.feedback
    if state.progress is not None:
        summary[f"{anchor_id}.progress"] = state.progress
