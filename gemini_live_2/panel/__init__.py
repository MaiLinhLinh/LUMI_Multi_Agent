"""Framework-neutral contracts for generated visual panels."""

from .contracts import (
    ActivePanelState,
    AnchorBinding,
    PanelBlock,
    PlanBlock,
    DataAlias,
    DataBundle,
    GridRect,
    PanelIR,
    PresentationPlan,
    RouteRequest,
)
from .compiler import CANVAS_COLUMNS, CANVAS_ROWS, PanelCompilationError, PanelCompiler
from .renderers import panel_client_payload, render_visual_stage_map

__all__ = [
    "ActivePanelState",
    "AnchorBinding",
    "PanelBlock",
    "PlanBlock",
    "DataAlias",
    "DataBundle",
    "GridRect",
    "PanelIR",
    "PresentationPlan",
    "RouteRequest",
    "CANVAS_COLUMNS",
    "CANVAS_ROWS",
    "PanelCompilationError",
    "PanelCompiler",
    "panel_client_payload",
    "render_visual_stage_map",
]
