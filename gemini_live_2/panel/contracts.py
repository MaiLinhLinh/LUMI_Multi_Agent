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
class PlanBlock:
    """A Plan Agent layout instruction without compiler-owned identifiers."""

    widget_id: str
    grid: GridRect
    props: Mapping[str, JsonValue] = field(default_factory=dict)
    initial_visibility: str = "visible"

    def __post_init__(self) -> None:
        object.__setattr__(self, "widget_id", _required_text(self.widget_id, "block.widget_id"))
        if not isinstance(self.grid, GridRect):
            raise ContractValidationError("block.grid must be a GridRect.")
        if not isinstance(self.props, Mapping):
            raise ContractValidationError("block.props must be an object.")
        object.__setattr__(self, "props", dict(self.props))
        if self.initial_visibility not in _VISIBILITY_STATES:
            raise ContractValidationError("block.initial_visibility must be 'visible' or 'hidden'.")

    @classmethod
    def from_dict(cls, value: object) -> "PlanBlock":
        data = _mapping(value, "block")
        props = data.get("props", {})
        return cls(
            widget_id=data.get("widget_id"),
            grid=GridRect.from_dict(data.get("grid")),
            props=_mapping(props, "block.props"),
            initial_visibility=data.get("initial_visibility", "visible"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "widget_id": self.widget_id,
            "grid": self.grid.to_dict(),
            "props": dict(self.props),
            "initial_visibility": self.initial_visibility,
        }


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
class PanelBlock:
    """Trusted materialized block with an identifier created by the Compiler."""

    id: str
    widget_id: str
    grid: GridRect
    props: Mapping[str, JsonValue] = field(default_factory=dict)
    visibility: str = "visible"

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "widget_id": self.widget_id,
            "grid": self.grid.to_dict(),
            "props": dict(self.props),
            "visibility": self.visibility,
        }


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
    target_id: str
    allowed_effect_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_id", _required_text(self.anchor_id, "anchor.anchor_id"))
        object.__setattr__(self, "block_id", _required_text(self.block_id, "anchor.block_id"))
        object.__setattr__(self, "anchor_key", _required_text(self.anchor_key, "anchor.anchor_key"))
        object.__setattr__(self, "target_id", _required_text(self.target_id, "anchor.target_id"))
        if not isinstance(self.allowed_effect_ids, tuple) or not self.allowed_effect_ids:
            raise ContractValidationError("anchor.allowed_effect_ids must not be empty.")
        effects = tuple(_required_text(effect, "anchor.allowed_effect_ids") for effect in self.allowed_effect_ids)
        if len(effects) != len(set(effects)):
            raise ContractValidationError("anchor.allowed_effect_ids contains duplicates.")
        object.__setattr__(self, "allowed_effect_ids", effects)


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
    """Minimal per-session panel state; semantic lesson state stays with Gemini/history."""

    panel_ir: PanelIR
    revision: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.panel_ir, PanelIR):
            raise ContractValidationError("active panel requires a PanelIR.")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ContractValidationError("active panel revision must be a positive integer.")

    def replace(self, panel_ir: PanelIR) -> "ActivePanelState":
        return ActivePanelState(panel_ir=panel_ir, revision=self.revision + 1)
