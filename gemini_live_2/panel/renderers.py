"""Render-neutral exports of a trusted PanelIR.

Both functions read the same materialized PanelIR.  The browser receives a
compact safe payload for its CSS Grid renderer, while Gemini Live receives an
ASCII stage map.  Neither renderer reinterprets a plan or changes layout.
"""

from __future__ import annotations

import textwrap
from typing import Any, Mapping

from .contracts import PanelBlock, PanelIR


def panel_client_payload(panel: PanelIR, *, asset_urls: Mapping[str, str]) -> dict[str, Any]:
    """Return only browser-safe PanelIR data and URLs for assets it actually uses."""

    used_asset_ids = {
        asset_id
        for block in panel.blocks
        if block.visibility == "visible" and isinstance((asset_id := block.props.get("asset_id")), str)
    }
    return {
        "ui_type": "panel_ir",
        "panel": {
            "panel_id": panel.panel_id,
            "domain_id": panel.domain_id,
            "blocks": [_client_block(block) for block in panel.blocks],
            "anchors": [
                {
                    "anchor_id": anchor.anchor_id,
                    "block_id": anchor.block_id,
                    "anchor_key": anchor.anchor_key,
                    "target_id": anchor.target_id,
                    "allowed_effect_ids": list(anchor.allowed_effect_ids),
                }
                for anchor in panel.anchors
            ],
        },
        "assets": [
            {"id": asset_id, "url": asset_urls[asset_id]}
            for asset_id in sorted(used_asset_ids)
            if isinstance(asset_urls.get(asset_id), str) and asset_urls[asset_id]
        ],
    }


def _client_block(block: PanelBlock) -> dict[str, Any]:
    """Redact hidden values from the initial browser payload."""

    data = block.to_dict()
    if block.visibility == "hidden":
        data["props"] = {}
    return data


def render_visual_stage_map(panel: PanelIR) -> str:
    """Render a spatial, text-only copy of the user-visible panel.

    This is intentionally not a character-art wireframe.  Borders and dense
    technical labels made the old map harder to read than the UI itself.  Each
    CSS-grid column becomes a fixed text track and every anchor is printed
    directly below the thing it refers to.
    """

    anchors_by_block = _anchors_by_block(panel)
    column_width = 8
    row_height = 4
    canvas = _draw_stage_canvas(
        panel.blocks,
        anchors_by_block,
        column_width=column_width,
        row_height=row_height,
    )

    rows = [
        "VISUAL STAGE MAP — MÀN HÌNH NGƯỜI DÙNG",
        "Bố cục CSS Grid 16 cột × 10 hàng; vị trí tương đối khớp vùng người dùng đang thấy.",
        "Mỗi [anchor: …] nằm ngay dưới vùng hoặc vật thể mà nó minh hoạ.",
        "",
    ]
    rows.extend("".join(line).rstrip() for line in canvas)
    return "\n".join(rows).rstrip()


def _anchors_by_block(panel: PanelIR) -> dict[str, tuple[str, ...]]:
    bindings: dict[str, list[tuple[str, str]]] = {}
    for anchor in panel.anchors:
        bindings.setdefault(anchor.block_id, []).append((anchor.anchor_key, anchor.anchor_id))
    return {
        block_id: tuple(anchor_id for _, anchor_id in sorted(items))
        for block_id, items in bindings.items()
    }


def _draw_stage_canvas(
    blocks: tuple[PanelBlock, ...],
    anchors_by_block: Mapping[str, tuple[str, ...]],
    *,
    column_width: int,
    row_height: int,
) -> list[list[str]]:
    """Place compact visible content by its real GridRect, without borders."""

    canvas_width = 16 * column_width
    canvas = [[" "] * canvas_width for _ in range(10 * row_height)]

    for block in sorted(blocks, key=lambda item: (item.grid.row, item.grid.col, item.id)):
        x = (block.grid.col - 1) * column_width
        y = (block.grid.row - 1) * row_height
        width = block.grid.col_span * column_width
        height = block.grid.row_span * row_height
        _place_region(
            canvas,
            x=x,
            y=y,
            width=width,
            height=height,
            content=_region_lines(block, anchors_by_block.get(block.id, ()), width),
        )
    return canvas


def _place_region(
    canvas: list[list[str]], *, x: int, y: int, width: int, height: int, content: list[str]
) -> None:
    """Centre compact region lines inside their own CSS-grid rectangle."""

    visible_lines = content[:height]
    start_row = y + max(0, (height - len(visible_lines)) // 2)
    for index, line in enumerate(visible_lines):
        row = start_row + index
        clipped = line[:width]
        start_column = x + max(0, (width - len(clipped)) // 2)
        for offset, char in enumerate(clipped):
            canvas[row][start_column + offset] = char


def _visible_region_content(block: PanelBlock) -> list[str]:
    """Return only information that the browser currently renders."""

    if block.visibility == "hidden":
        return ["ĐANG ẨN"]
    if block.widget_id == "text":
        return [str(block.props.get("content", ""))]
    if block.widget_id == "image":
        return ["ẢNH", str(block.props.get("asset_id", ""))]
    if block.widget_id == "object_group":
        return ["NHÓM", f"{block.props.get('count', 0)} × {block.props.get('asset_id', '')}"]
    if block.widget_id == "answer":
        return ["KẾT QUẢ", str(block.props.get("value", ""))]
    if block.widget_id == "number_display":
        return ["SỐ", str(block.props.get("value", ""))]
    return [f"[{block.widget_id}]"]


def _region_lines(block: PanelBlock, anchor_ids: tuple[str, ...], width: int) -> list[str]:
    """Describe one region as it appears, with anchors directly underneath."""

    if block.visibility == "hidden":
        return _wrapped_lines("NỘI DUNG ĐANG ẨN", width) + _anchor_lines(anchor_ids, width)

    if block.widget_id == "object_group":
        return _object_group_lines(block, anchor_ids, width)

    lines: list[str] = []
    for value in _visible_region_content(block):
        lines.extend(_wrapped_lines(str(value), width))
    return lines + _anchor_lines(anchor_ids, width)


def _object_group_lines(block: PanelBlock, anchor_ids: tuple[str, ...], width: int) -> list[str]:
    """Show every object and its own anchor on the line immediately below it."""

    count = max(0, int(block.props.get("count", 0)))
    asset_id = str(block.props.get("asset_id", ""))
    group_anchor = anchor_ids[0] if anchor_ids else None
    item_anchors = list(anchor_ids[1:])

    lines = _wrapped_lines(f"NHÓM: {count} × {asset_id}", width)
    if count <= 0:
        return lines + _anchor_lines((group_anchor,) if group_anchor else (), width)

    item_text = f"ẢNH: {asset_id}"
    anchor_texts = [f"[anchor: {anchor_id}]" for anchor_id in item_anchors[:count]]
    while len(anchor_texts) < count:
        anchor_texts.append("")
    cell_width = max(len(item_text), *(len(item) for item in anchor_texts), 1) + 2
    items_per_row = max(1, width // cell_width)

    for start in range(0, count, items_per_row):
        visible_items = min(items_per_row, count - start)
        item_row = "".join(item_text.center(cell_width) for _ in range(visible_items)).rstrip()
        anchor_row = "".join(anchor_texts[start + index].center(cell_width) for index in range(visible_items)).rstrip()
        lines.extend((item_row, anchor_row))

    if group_anchor:
        lines.append(f"Nhóm này: [anchor: {group_anchor}]")
    return lines


def _anchor_lines(anchor_ids: tuple[str | None, ...], width: int) -> list[str]:
    """Keep direct anchor syntax readable without a separate technical key."""

    anchors = [f"[anchor: {anchor_id}]" for anchor_id in anchor_ids if anchor_id]
    if not anchors:
        return []
    lines: list[str] = []
    current = ""
    for anchor in anchors:
        candidate = f"{current}  {anchor}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = anchor
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _wrapped_lines(value: str, width: int) -> list[str]:
    return textwrap.wrap(value, width=max(1, width), break_long_words=True, break_on_hyphens=False) or [""]
