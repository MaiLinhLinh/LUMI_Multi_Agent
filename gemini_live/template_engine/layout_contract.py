"""Trusted, domain-neutral contract for Template LLM grid layouts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


CANVAS_COLUMNS = 12
CANVAS_ROWS = 10
_BLOCK_TYPES = frozenset({"text", "image"})
_MAX_TEXT_LENGTH = 160
_MAX_LABEL_LENGTH = 80


class LayoutSpecValidationError(ValueError):
    """Raised when a Template LLM response is outside the allowed contract."""


@dataclass(frozen=True)
class GridPlacement:
    col: int
    row: int
    col_span: int
    row_span: int

    @property
    def last_col(self) -> int:
        return self.col + self.col_span - 1

    @property
    def last_row(self) -> int:
        return self.row + self.row_span - 1

    def overlaps(self, other: "GridPlacement") -> bool:
        return not (
            self.last_col < other.col
            or other.last_col < self.col
            or self.last_row < other.row
            or other.last_row < self.row
        )


@dataclass(frozen=True)
class TextBlock:
    id: str
    content: str
    grid: GridPlacement
    type: str = "text"


@dataclass(frozen=True)
class ImageBlock:
    id: str
    asset_id: str
    label: str
    grid: GridPlacement
    type: str = "image"


LayoutBlock = TextBlock | ImageBlock


@dataclass(frozen=True)
class LayoutSpec:
    columns: int
    rows: int
    blocks: tuple[LayoutBlock, ...]


def layout_spec_to_dict(spec: LayoutSpec) -> dict[str, object]:
    """Serialize one validated spec for the trusted Dynamic Grid payload."""

    blocks: list[dict[str, object]] = []
    for block in spec.blocks:
        item: dict[str, object] = {
            "id": block.id,
            "type": block.type,
            "grid": {
                "col": block.grid.col,
                "row": block.grid.row,
                "col_span": block.grid.col_span,
                "row_span": block.grid.row_span,
            },
        }
        if isinstance(block, TextBlock):
            item["content"] = block.content
        elif isinstance(block, ImageBlock):
            item["asset_id"] = block.asset_id
            item["label"] = block.label
        blocks.append(item)
    return {"canvas": {"columns": spec.columns, "rows": spec.rows}, "blocks": blocks}


def validate_layout_spec(payload: object, *, allowed_asset_ids: Iterable[str]) -> LayoutSpec:
    """Parse a complete internal Layout Spec into its trusted form."""

    assets = frozenset(allowed_asset_ids)
    document = _mapping(payload, "layout spec")
    _require_exact_keys(document, {"canvas", "blocks"}, "layout spec")
    canvas = _mapping(document["canvas"], "canvas")
    _require_exact_keys(canvas, {"columns", "rows"}, "canvas")
    columns = _positive_int(canvas["columns"], "canvas.columns")
    rows = _positive_int(canvas["rows"], "canvas.rows")
    if (columns, rows) != (CANVAS_COLUMNS, CANVAS_ROWS):
        raise LayoutSpecValidationError(
            f"canvas must be exactly {CANVAS_COLUMNS} columns by {CANVAS_ROWS} rows."
        )
    raw_blocks = document["blocks"]
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise LayoutSpecValidationError("blocks must be a non-empty array.")
    parsed_blocks = tuple(_parse_block(raw, assets) for raw in raw_blocks)
    _validate_unique_ids(parsed_blocks)
    _validate_bounds(parsed_blocks)
    _validate_overlap_rules(parsed_blocks)
    return LayoutSpec(columns=columns, rows=rows, blocks=parsed_blocks)


def validate_template_layout_output(payload: object, *, allowed_asset_ids: Iterable[str]) -> LayoutSpec:
    """Validate the compact object returned by a Template LLM.

    The backend owns the fixed canvas; a model may return only the blocks.
    """

    output = _mapping(payload, "template layout output")
    _require_exact_keys(output, {"blocks"}, "template layout output")
    return validate_layout_spec(
        {"canvas": {"columns": CANVAS_COLUMNS, "rows": CANVAS_ROWS}, "blocks": output["blocks"]},
        allowed_asset_ids=allowed_asset_ids,
    )


def _parse_block(raw: object, assets: frozenset[str]) -> LayoutBlock:
    block = _mapping(raw, "block")
    block_type = _short_string(block.get("type"), "block.type")
    if block_type not in _BLOCK_TYPES:
        raise LayoutSpecValidationError("block.type must be text or image.")
    common_keys = {"id", "type", "grid"}
    if block_type == "text":
        _require_exact_keys(block, common_keys | {"content"}, "text block")
        return TextBlock(
            id=_short_string(block["id"], "text block.id"),
            content=_short_string(block["content"], "text block.content", _MAX_TEXT_LENGTH),
            grid=_parse_grid(block["grid"]),
        )
    _require_exact_keys(block, common_keys | {"asset_id", "label"}, "image block")
    asset_id = _short_string(block["asset_id"], "image block.asset_id")
    if asset_id not in assets:
        raise LayoutSpecValidationError(f"image block.asset_id is not catalogued: {asset_id!r}.")
    return ImageBlock(
        id=_short_string(block["id"], "image block.id"),
        asset_id=asset_id,
        label=_short_string(block["label"], "image block.label", _MAX_LABEL_LENGTH),
        grid=_parse_grid(block["grid"]),
    )


def _parse_grid(raw: object) -> GridPlacement:
    grid = _mapping(raw, "block.grid")
    _require_exact_keys(grid, {"col", "row", "col_span", "row_span"}, "block.grid")
    return GridPlacement(
        col=_positive_int(grid["col"], "block.grid.col"),
        row=_positive_int(grid["row"], "block.grid.row"),
        col_span=_positive_int(grid["col_span"], "block.grid.col_span"),
        row_span=_positive_int(grid["row_span"], "block.grid.row_span"),
    )


def _validate_unique_ids(blocks: tuple[LayoutBlock, ...]) -> None:
    if len({block.id for block in blocks}) != len(blocks):
        raise LayoutSpecValidationError("block ids must be unique.")


def _validate_bounds(blocks: tuple[LayoutBlock, ...]) -> None:
    for block in blocks:
        if block.grid.last_col > CANVAS_COLUMNS or block.grid.last_row > CANVAS_ROWS:
            raise LayoutSpecValidationError(f"block {block.id!r} exceeds the {CANVAS_COLUMNS}x{CANVAS_ROWS} canvas.")


def _validate_overlap_rules(blocks: tuple[LayoutBlock, ...]) -> None:
    for index, first in enumerate(blocks):
        for second in blocks[index + 1:]:
            if first.grid.overlaps(second.grid):
                raise LayoutSpecValidationError("text and image blocks must not overlap.")


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LayoutSpecValidationError(f"{name} must be an object.")
    return value


def _require_exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual == expected:
        return
    details: list[str] = []
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        details.append(f"missing: {', '.join(missing)}")
    if unknown:
        details.append(f"unknown: {', '.join(unknown)}")
    raise LayoutSpecValidationError(f"{name} has invalid fields ({'; '.join(details)}).")


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise LayoutSpecValidationError(f"{name} must be a positive integer.")
    return value


def _short_string(value: object, name: str, max_length: int = 64) -> str:
    if not isinstance(value, str):
        raise LayoutSpecValidationError(f"{name} must be a string.")
    text = value.strip()
    if not text or len(text) > max_length:
        raise LayoutSpecValidationError(f"{name} must be between 1 and {max_length} characters.")
    return text
