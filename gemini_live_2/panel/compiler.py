"""Deterministic validation and materialization of a PresentationPlan.

The compiler has no LLM, domain-tool or layout-selection responsibility.  It
accepts a plan already chosen by a Plan Agent, verifies it against declarative
domain resources, resolves only explicitly published aliases, and creates the
trusted PanelIR consumed by both renderers in the next checkpoint.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping
from uuid import uuid4

from gemini_live_2.widgets import WidgetPropsError, WidgetRegistry

from .contracts import (
    AnchorBinding,
    DataBundle,
    GridRect,
    PanelBlock,
    PanelChoiceChild,
    PanelIR,
    PlanBlock,
    PresentationPlan,
)

if TYPE_CHECKING:
    from gemini_live_2.catalogs.domains import DomainResources


CANVAS_COLUMNS = 16
CANVAS_ROWS = 10


class PanelCompilationError(ValueError):
    """A plan cannot safely become a PanelIR for the selected domain."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_plan",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})

    def for_plan_agent(self) -> dict[str, Any]:
        """Return safe, actionable validation feedback for the planning model."""

        return {
            "status": "invalid_plan",
            "error_code": self.code,
            "message": str(self),
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class PanelCompiler:
    """Compile a plan using only registered widgets and a domain's resources."""

    widget_registry: WidgetRegistry
    canvas_columns: int = CANVAS_COLUMNS
    canvas_rows: int = CANVAS_ROWS

    def __post_init__(self) -> None:
        for field_name in ("canvas_columns", "canvas_rows"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer.")

    def compile(
        self,
        *,
        plan: PresentationPlan,
        data_bundle: DataBundle,
        domain_resources: DomainResources,
        panel_id: str | None = None,
        block_ids: tuple[str, ...] | None = None,
        anchor_ids_by_block_key: Mapping[tuple[str, str], str] | None = None,
    ) -> PanelIR:
        """Validate and materialize a plan without choosing or altering its layout.

        ``block_ids`` and ``anchor_ids_by_block_key`` are supplied only by the
        Runtime when applying a structural patch to an existing surface.  They
        preserve compiler-owned identities for components that survive the
        patch; the Plan Agent never chooses either kind of ID.
        """

        if plan.domain_id != data_bundle.domain_id:
            raise PanelCompilationError("plan.domain_id must match data_bundle.domain_id.")
        if plan.domain_id != domain_resources.manifest.domain_id:
            raise PanelCompilationError("plan.domain_id must match domain resources.")
        if block_ids is not None:
            if len(block_ids) != len(plan.blocks) or any(not isinstance(item, str) or not item for item in block_ids):
                raise PanelCompilationError("runtime block identities must match the plan blocks.")
            if len(set(block_ids)) != len(block_ids):
                raise PanelCompilationError("runtime block identities must be unique.")

        self._validate_grid(plan.blocks)
        aliases = self._alias_values(data_bundle)
        materialized_blocks: list[PanelBlock] = []
        anchor_requests: list[tuple[PanelBlock, Any]] = []

        for index, block in enumerate(plan.blocks, start=1):
            if block.widget_id not in domain_resources.manifest.allowed_widget_ids:
                raise PanelCompilationError(
                    f"widget '{block.widget_id}' is not allowed by domain '{plan.domain_id}'."
                )
            try:
                widget = self.widget_registry.get(block.widget_id)
                resolved_props = _resolve_aliases(block.props, aliases)
                normalized_props = widget.validate(resolved_props)
            except WidgetPropsError as error:
                raise PanelCompilationError(str(error)) from error

            self._validate_asset_reference(
                widget_id=block.widget_id,
                props=normalized_props,
                domain_resources=domain_resources,
            )
            children = self._materialize_choice_children(
                block=block,
                allowed_child_widget_ids=widget.allowed_child_widget_ids,
                aliases=aliases,
                domain_resources=domain_resources,
            )
            materialized = PanelBlock(
                id=block_ids[index - 1] if block_ids is not None else str(index),
                widget_id=block.widget_id,
                grid=block.grid,
                props=normalized_props,
                visibility=block.initial_visibility,
                children=children,
            )
            materialized_blocks.append(materialized)
            anchor_requests.extend((materialized, anchor) for anchor in widget.anchors_for(materialized.props))

        resolved_panel_id = panel_id or f"panel-{uuid4().hex}"
        anchors = self._materialize_anchors(
            anchor_requests=anchor_requests,
            existing_ids=anchor_ids_by_block_key or {},
        )

        return PanelIR(
            panel_id=resolved_panel_id,
            domain_id=plan.domain_id,
            blocks=tuple(materialized_blocks),
            anchors=anchors,
        )

    @staticmethod
    def _materialize_anchors(
        *,
        anchor_requests: list[tuple[PanelBlock, Any]],
        existing_ids: Mapping[tuple[str, str], str],
    ) -> tuple[AnchorBinding, ...]:
        """Keep valid existing anchors and mint only identities a patch adds."""

        used_ids = set(existing_ids.values())
        next_index = 0

        def allocate() -> str:
            nonlocal next_index
            while True:
                candidate = _short_anchor_id(next_index)
                next_index += 1
                if candidate not in used_ids:
                    used_ids.add(candidate)
                    return candidate

        bindings: list[AnchorBinding] = []
        for block, anchor in anchor_requests:
            identity_key = (block.id, anchor.key)
            anchor_id = existing_ids.get(identity_key) or allocate()
            bindings.append(AnchorBinding(
                anchor_id=anchor_id,
                block_id=block.id,
                anchor_key=anchor.key,
                allowed_effect_ids=anchor.allowed_effect_ids,
            ))
        return tuple(bindings)

    def _materialize_choice_children(
        self,
        *,
        block: PlanBlock,
        allowed_child_widget_ids: tuple[str, ...],
        aliases: Mapping[str, Any],
        domain_resources: DomainResources,
    ) -> tuple[PanelChoiceChild, ...]:
        """Validate and materialize nested widgets without assigning them grid cells.

        Only a registered container widget declares allowed child widget IDs.
        At this stage choice is the sole container, so children cannot acquire
        their own anchors or interaction semantics.
        """

        if not block.children:
            if block.widget_id == "choice":
                raise PanelCompilationError("choice block must contain at least one child.")
            return ()
        if not allowed_child_widget_ids:
            raise PanelCompilationError(f"widget '{block.widget_id}' does not allow children.")

        materialized: list[PanelChoiceChild] = []
        for child_index, child in enumerate(block.children, start=1):
            if child.widget_id not in allowed_child_widget_ids:
                allowed = ", ".join(allowed_child_widget_ids)
                raise PanelCompilationError(
                    f"choice child {child_index} widget '{child.widget_id}' is not allowed; "
                    f"allowed: {allowed}."
                )
            if child.widget_id not in domain_resources.manifest.allowed_widget_ids:
                raise PanelCompilationError(
                    f"choice child {child_index} widget '{child.widget_id}' is not allowed "
                    f"by the active domain."
                )
            try:
                widget = self.widget_registry.get(child.widget_id)
                props = widget.validate(_resolve_aliases(child.props, aliases))
            except WidgetPropsError as error:
                raise PanelCompilationError(f"choice child {child_index}: {error}") from error
            self._validate_asset_reference(
                widget_id=child.widget_id,
                props=props,
                domain_resources=domain_resources,
            )
            materialized.append(PanelChoiceChild(widget_id=child.widget_id, props=props))
        return tuple(materialized)

    def _validate_grid(self, blocks: tuple[PlanBlock, ...]) -> None:
        for index, block in enumerate(blocks, start=1):
            if block.grid.col + block.grid.col_span - 1 > self.canvas_columns:
                raise PanelCompilationError(
                    f"block {index} exceeds canvas column boundary.",
                    code="grid_out_of_bounds",
                    details={
                        "block_index": index,
                        "axis": "column",
                        "max": self.canvas_columns,
                        "actual_end": block.grid.col + block.grid.col_span - 1,
                    },
                )
            if block.grid.row + block.grid.row_span - 1 > self.canvas_rows:
                raise PanelCompilationError(
                    f"block {index} exceeds canvas row boundary.",
                    code="grid_out_of_bounds",
                    details={
                        "block_index": index,
                        "axis": "row",
                        "max": self.canvas_rows,
                        "actual_end": block.grid.row + block.grid.row_span - 1,
                    },
                )

        for left_index, left in enumerate(blocks):
            for right_index, right in enumerate(blocks[left_index + 1 :], start=left_index + 2):
                if _rectangles_overlap(left.grid, right.grid):
                    raise PanelCompilationError(
                        f"blocks {left_index + 1} and {right_index} overlap.",
                        code="grid_overlap",
                        details={
                            "first_block_index": left_index + 1,
                            "second_block_index": right_index,
                            "overlap_cells": _overlap_cells(left.grid, right.grid),
                        },
                    )

    @staticmethod
    def _alias_values(data_bundle: DataBundle) -> dict[str, Any]:
        aliases: dict[str, Any] = {}
        for alias in data_bundle.aliases:
            value: Any = data_bundle.data
            for part in alias.path:
                if not isinstance(value, Mapping) or part not in value:
                    raise PanelCompilationError(f"data alias '{alias.id}' does not resolve from DataBundle.")
                value = value[part]
            aliases[alias.id] = deepcopy(value)
        return aliases

    @staticmethod
    def _validate_asset_reference(
        *,
        widget_id: str,
        props: Mapping[str, Any],
        domain_resources: DomainResources,
    ) -> None:
        asset_id = props.get("asset_id")
        if asset_id is None:
            return
        try:
            asset = domain_resources.assets.get(asset_id)
        except Exception as error:  # Catalog errors become one compiler boundary error.
            raise PanelCompilationError(f"unknown asset_id '{asset_id}' for this domain.") from error
        allowed_kinds = {"image", "icon"} if widget_id == "image" else {"image"}
        if asset.kind not in allowed_kinds:
            allowed = " or ".join(sorted(allowed_kinds))
            raise PanelCompilationError(
                f"asset_id '{asset_id}' must be an {allowed} asset for widget '{widget_id}'."
            )

def _rectangles_overlap(left: GridRect, right: GridRect) -> bool:
    return not (
        left.col + left.col_span <= right.col
        or right.col + right.col_span <= left.col
        or left.row + left.row_span <= right.row
        or right.row + right.row_span <= left.row
    )


def _overlap_cells(left: GridRect, right: GridRect) -> list[dict[str, int]]:
    """List the shared one-based grid cells for an actionable planner error."""

    first_col = max(left.col, right.col)
    last_col = min(left.col + left.col_span - 1, right.col + right.col_span - 1)
    first_row = max(left.row, right.row)
    last_row = min(left.row + left.row_span - 1, right.row + right.row_span - 1)
    return [
        {"col": col, "row": row}
        for row in range(first_row, last_row + 1)
        for col in range(first_col, last_col + 1)
    ]


def _resolve_aliases(value: Any, aliases: Mapping[str, Any]) -> Any:
    """Resolve an exact `$alias` recursively; no implicit data-path syntax."""

    if isinstance(value, str) and value.startswith("$"):
        try:
            return deepcopy(aliases[value])
        except KeyError as error:
            raise PanelCompilationError(f"unknown data alias '{value}'.") from error
    if isinstance(value, Mapping):
        return {str(key): _resolve_aliases(child, aliases) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve_aliases(child, aliases) for child in value]
    return deepcopy(value)


def _short_anchor_id(index: int) -> str:
    """Return a compact spreadsheet-style label: a…z, aa…az, ba…"""

    value = index + 1
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("a") + remainder) + letters
    return letters
