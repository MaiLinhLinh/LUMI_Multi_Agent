"""Deterministic validation and materialization of a PresentationPlan.

The compiler has no LLM, domain-tool or layout-selection responsibility.  It
accepts a plan already chosen by a Plan Agent, verifies it against declarative
domain resources, resolves only explicitly published aliases, and creates one
trusted ``SurfaceDocument`` render contract.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping
from uuid import uuid4

from gemini_live_2.widgets import WidgetPropsError, WidgetRegistry

from .contracts import (
    AnchorBinding,
    ComponentChild,
    ComponentNode,
    DataBundle,
    GridRect,
    PlanBlock,
    PresentationPlan,
    SurfaceDocument,
)

if TYPE_CHECKING:
    from gemini_live_2.catalogs.domains import DomainResources


CANVAS_COLUMNS = 16
CANVAS_ROWS = 10


class PanelCompilationError(ValueError):
    """A plan cannot safely become a SurfaceDocument for the selected domain."""

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

    def compile_surface_document(
        self,
        *,
        plan: PresentationPlan,
        data_bundle: DataBundle,
        domain_resources: DomainResources,
        surface_id: str | None = None,
        component_ids: tuple[str, ...] | None = None,
        anchor_ids_by_component_key: Mapping[tuple[str, str], str] | None = None,
    ) -> SurfaceDocument:
        """Validate a plan into the SD3 ``SurfaceDocument`` contract.

        The optional identities are Runtime-owned inputs for a later structural
        patch; a new surface receives compiler-owned sequential component and
        anchor identities, and always starts at revision 1.
        """

        if plan.domain_id != data_bundle.domain_id:
            raise PanelCompilationError("plan.domain_id must match data_bundle.domain_id.")
        if plan.domain_id != domain_resources.manifest.domain_id:
            raise PanelCompilationError("plan.domain_id must match domain resources.")
        if component_ids is not None:
            if len(component_ids) != len(plan.blocks) or any(
                not isinstance(item, str) or not item for item in component_ids
            ):
                raise PanelCompilationError("runtime component identities must match the plan blocks.")
            if len(set(component_ids)) != len(component_ids):
                raise PanelCompilationError("runtime component identities must be unique.")

        self._validate_grid(plan.blocks)
        aliases = self._alias_values(data_bundle)
        components: list[ComponentNode] = []
        anchor_requests: list[tuple[ComponentNode, Any]] = []

        for index, block in enumerate(plan.blocks, start=1):
            if block.widget_id not in domain_resources.manifest.allowed_widget_ids:
                raise PanelCompilationError(
                    f"widget '{block.widget_id}' is not allowed by domain '{plan.domain_id}'."
                )
            try:
                widget = self.widget_registry.get(block.widget_id)
                normalized_props = widget.validate(_resolve_aliases(block.props, aliases))
                initial_state = self._materialize_initial_state(block=block, widget=widget)
            except WidgetPropsError as error:
                raise PanelCompilationError(str(error)) from error

            self._validate_asset_references(
                widget=widget,
                props=normalized_props,
                domain_resources=domain_resources,
            )
            children = self._materialize_component_children(
                block=block,
                aliases=aliases,
                domain_resources=domain_resources,
            )
            component = ComponentNode(
                id=component_ids[index - 1] if component_ids is not None else str(index),
                type=block.widget_id,
                layout=block.grid,
                props=normalized_props,
                state=initial_state,
                children=children,
            )
            components.append(component)
            anchor_requests.extend((component, anchor) for anchor in widget.anchors_for(component.props))

        resolved_surface_id = surface_id or f"panel-{uuid4().hex}"
        anchors = self._materialize_anchors(
            anchor_requests=anchor_requests,
            existing_ids=anchor_ids_by_component_key or {},
        )
        return SurfaceDocument(
            surface_id=resolved_surface_id,
            domain_id=plan.domain_id,
            revision=1,
            components=tuple(components),
            anchors=anchors,
        )

    @staticmethod
    def _materialize_anchors(
        *,
        anchor_requests: list[tuple[ComponentNode, Any]],
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
        for component, anchor in anchor_requests:
            identity_key = (component.id, anchor.key)
            anchor_id = existing_ids.get(identity_key) or allocate()
            bindings.append(AnchorBinding(
                anchor_id=anchor_id,
                component_id=component.id,
                anchor_key=anchor.key,
                allowed_effect_ids=anchor.allowed_effect_ids,
            ))
        return tuple(bindings)

    @staticmethod
    def _materialize_initial_state(*, block: PlanBlock, widget: Any) -> dict[str, Any]:
        """Merge the universal visibility baseline into Registry-owned state."""

        requested_state = dict(block.initial_state)
        requested_state.setdefault("visibility", block.initial_visibility)
        return widget.materialize_initial_state(requested_state)

    def _materialize_component_children(
        self,
        *,
        block: PlanBlock,
        aliases: Mapping[str, Any],
        domain_resources: DomainResources,
    ) -> tuple[ComponentChild, ...]:
        """Validate child widgets for a document component without grid cells.

        The parent Widget Registry defines legal child types. Children retain
        renderer-facing type and validated props only; they receive neither a
        top-level component identity nor their own anchor in this phase.
        """

        if not block.children:
            if block.widget_id == "choice":
                raise PanelCompilationError("choice block must contain at least one child.")
            return ()
        try:
            parent = self.widget_registry.get(block.widget_id)
            child_widget_ids = parent.validate_child_widget_ids(
                tuple(child.widget_id for child in block.children)
            )
        except WidgetPropsError as error:
            raise PanelCompilationError(str(error)) from error

        materialized: list[ComponentChild] = []
        for child_index, (child, child_widget_id) in enumerate(
            zip(block.children, child_widget_ids, strict=True), start=1
        ):
            if child_widget_id not in domain_resources.manifest.allowed_widget_ids:
                raise PanelCompilationError(
                    f"component child {child_index} widget '{child_widget_id}' is not allowed "
                    f"by the active domain."
                )
            try:
                child_widget = self.widget_registry.get(child_widget_id)
                child_props = child_widget.validate(_resolve_aliases(child.props, aliases))
            except WidgetPropsError as error:
                raise PanelCompilationError(f"component child {child_index}: {error}") from error
            self._validate_asset_references(
                widget=child_widget,
                props=child_props,
                domain_resources=domain_resources,
            )
            materialized.append(ComponentChild(type=child_widget_id, props=child_props))
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
    def _validate_asset_references(
        *,
        widget: Any,
        props: Mapping[str, Any],
        domain_resources: DomainResources,
    ) -> None:
        """Validate only the asset paths explicitly declared by the widget.

        Asset references can be nested (for example flashcard.front.asset_id),
        so the compiler must not infer paths from widget IDs or arbitrary props.
        """

        for reference in widget.asset_references:
            asset_id = _resolve_props_path(props, reference.path)
            if not isinstance(asset_id, str) or not asset_id:
                raise PanelCompilationError(
                    f"asset reference '{reference.path}' for widget '{widget.widget_id}' must resolve to a string."
                )
            try:
                asset = domain_resources.assets.get(asset_id)
            except Exception as error:  # Catalog errors become one compiler boundary error.
                raise PanelCompilationError(f"unknown asset_id '{asset_id}' for this domain.") from error
            if asset.kind not in set(reference.allowed_kinds):
                allowed = " or ".join(sorted(reference.allowed_kinds))
                raise PanelCompilationError(
                    f"asset_id '{asset_id}' must be an {allowed} asset for widget '{widget.widget_id}'."
                )


def _resolve_props_path(props: Mapping[str, Any], path: str) -> Any:
    current: Any = props
    parts = path.split(".")
    if parts and parts[0] == "props":
        parts = parts[1:]
    for part in parts:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current

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
