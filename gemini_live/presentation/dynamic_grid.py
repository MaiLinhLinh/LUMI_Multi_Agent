"""Presentation contracts for validated block/grid layouts.

Dynamic Grid panels are intentionally separate from Jinja templates.  The
backend receives an already validated Layout Spec, resolves public asset URLs,
and passes a structured panel payload to the frontend renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DynamicGridAsset:
    """One browser-loadable asset available to a Dynamic Grid panel."""

    id: str
    url: str

    def to_panel_data(self) -> dict[str, str]:
        return {"id": self.id, "url": self.url}


@dataclass(frozen=True)
class DynamicGridPresentation:
    """A trusted, non-Jinja presentation prepared from a validated Layout Spec."""

    domain_id: str
    layout_spec: dict[str, object]
    assets: tuple[DynamicGridAsset, ...]
    presentation_instruction: str = ""
    stage_goal: str = ""
    presentation_id: str = "dynamic_grid"


@dataclass(frozen=True)
class PreparedDynamicGridPresentation:
    """Frontend-neutral panel payload for the future generic Grid Renderer."""

    panel: dict[str, object]
    panel_anchor_map: dict[str, dict[str, object]]
    visual_stage_map: str
    supported_effects: list[dict[str, str]]
    effect_id_map: dict[str, str]


_ANCHORABLE_BLOCK_TYPES = frozenset({"image"})
_SUPPORTED_EFFECTS = (
    ("highlight", "highlight", "Làm nổi bật nhẹ vùng đang được giải thích."),
    ("circle", "draw_circle", "Vẽ vòng tròn quanh một vùng cụ thể cần chú ý."),
)


def prepare_dynamic_grid(presentation: DynamicGridPresentation) -> PreparedDynamicGridPresentation:
    """Create the panel payload without reading or rendering a Jinja template."""

    if not presentation.domain_id.strip():
        raise ValueError("dynamic grid presentation requires domain_id")
    _validate_layout_payload(presentation.layout_spec)
    _validate_assets(presentation.assets)
    panel_anchor_map, presentation_targets = _build_interaction_contract(presentation.layout_spec)
    return PreparedDynamicGridPresentation(
        panel={
            "ui_type": "grid_layout",
            "domain_id": presentation.domain_id,
            "layout_spec": presentation.layout_spec,
            "assets": [asset.to_panel_data() for asset in presentation.assets],
            "presentation_targets": presentation_targets,
        },
        panel_anchor_map=panel_anchor_map,
        visual_stage_map=_render_visual_stage_map(
            presentation.layout_spec,
            panel_anchor_map,
            stage_goal=presentation.stage_goal,
        ),
        supported_effects=[
            {"id": effect_id, "description": description}
            for effect_id, _implementation, description in _SUPPORTED_EFFECTS
        ],
        effect_id_map={
            effect_id: implementation
            for effect_id, implementation, _description in _SUPPORTED_EFFECTS
        },
    )


def _validate_layout_payload(layout_spec: object) -> None:
    if not isinstance(layout_spec, dict):
        raise ValueError("dynamic grid layout_spec must be an object")
    canvas = layout_spec.get("canvas")
    blocks = layout_spec.get("blocks")
    if not isinstance(canvas, dict) or not isinstance(blocks, list) or not blocks:
        raise ValueError("dynamic grid layout_spec must contain canvas and blocks")


def _validate_assets(assets: tuple[DynamicGridAsset, ...]) -> None:
    seen: set[str] = set()
    for asset in assets:
        if not asset.id or asset.id in seen:
            raise ValueError("dynamic grid asset ids must be non-empty and unique")
        if not asset.url.startswith("/assets/education/"):
            raise ValueError("dynamic grid asset URL must use the Education public namespace")
        seen.add(asset.id)


def _build_interaction_contract(
    layout_spec: dict[str, object],
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    """Create server-owned anchors only for blocks that are visual objects.

    Text blocks remain narration context, not animation targets. Future widget
    types join ``_ANCHORABLE_BLOCK_TYPES`` through a
    renderer-owned capability registration, never through Template LLM output.
    """

    anchor_map: dict[str, dict[str, object]] = {}
    presentation_targets: dict[str, str] = {}
    blocks = layout_spec["blocks"]
    assert isinstance(blocks, list)  # checked by _validate_layout_payload
    anchor_index = 0
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in _ANCHORABLE_BLOCK_TYPES:
            continue
        block_id = block.get("id")
        if not isinstance(block_id, str) or not block_id:
            raise ValueError("anchorable dynamic grid block requires id")
        anchor_id = _alphabetic_anchor(anchor_index)
        anchor_index += 1
        target_id = f"dynamic-grid.block.{block_id}"
        anchor_map[anchor_id] = {
            "target_id": target_id,
            "allowed_effect_ids": [effect_id for effect_id, _implementation, _description in _SUPPORTED_EFFECTS],
        }
        presentation_targets[block_id] = target_id
    return anchor_map, presentation_targets


def _alphabetic_anchor(index: int) -> str:
    """Return a, b, … z, aa, ab, … for a non-negative index."""

    if index < 0:
        raise ValueError("anchor index must be non-negative")
    result = ""
    current = index
    while True:
        result = chr(ord("a") + (current % 26)) + result
        current = current // 26 - 1
        if current < 0:
            return result


def _render_visual_stage_map(
    layout_spec: dict[str, object],
    panel_anchor_map: dict[str, dict[str, object]],
    *,
    stage_goal: str,
) -> str:
    """Describe the actual grid layout, values, and interactive objects to Live."""

    blocks = layout_spec["blocks"]
    assert isinstance(blocks, list)  # checked by _validate_layout_payload
    target_to_anchor = {
        evidence["target_id"]: anchor_id
        for anchor_id, evidence in panel_anchor_map.items()
        if isinstance(evidence.get("target_id"), str)
    }
    ordered = sorted(
        (block for block in blocks if isinstance(block, dict)),
        key=lambda block: (_grid_start(block, "row"), _grid_start(block, "col")),
    )
    lines = [
        "VISUAL STAGE MAP — MÀN HÌNH HỌC DẠNG GRID",
        "Canvas: 12 cột × 10 hàng. Toạ độ mô tả đúng vị trí tương đối trên màn hình.",
    ]
    if stage_goal.strip():
        lines.extend(["", "MỤC TIÊU LƯỢT NÀY", stage_goal.strip()])
    lines.extend(["", "BỐ CỤC ĐANG HIỂN THỊ"])
    for block in ordered:
        grid = block.get("grid")
        assert isinstance(grid, dict)
        position = _grid_description(grid)
        block_type = block.get("type")
        if block_type == "text":
            lines.append(f"- {position}: Văn bản: {block.get('content', '')}")
        elif block_type == "image":
            block_id = block.get("id")
            target_id = f"dynamic-grid.block.{block_id}"
            anchor_id = target_to_anchor.get(target_id)
            label = block.get("label", "")
            asset_id = block.get("asset_id", "")
            anchor_text = f" [anchor: {anchor_id}]" if anchor_id else ""
            lines.append(f"- {position}: Hình {label} [asset: {asset_id}]{anchor_text}")

    if panel_anchor_map:
        lines.extend(["", "ANCHOR LEGEND"])
        for anchor_id, evidence in panel_anchor_map.items():
            target_id = evidence["target_id"]
            matching = next(
                (block for block in ordered if f"dynamic-grid.block.{block.get('id')}" == target_id),
                None,
            )
            label = matching.get("label") if isinstance(matching, dict) else None
            lines.append(f"{anchor_id} = vùng hình {label or 'trực quan'}")
    return "\n".join(lines)


def _grid_start(block: dict[str, object], field: str) -> int:
    grid = block.get("grid")
    return grid.get(field, 0) if isinstance(grid, dict) and isinstance(grid.get(field), int) else 0


def _grid_description(grid: dict[str, object]) -> str:
    col = grid["col"]
    row = grid["row"]
    col_span = grid["col_span"]
    row_span = grid["row_span"]
    assert all(isinstance(value, int) for value in (col, row, col_span, row_span))
    return f"Hàng {row}–{row + row_span - 1}, cột {col}–{col + col_span - 1}"
