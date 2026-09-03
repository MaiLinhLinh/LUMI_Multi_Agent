"""Render-neutral exports of trusted SurfaceDocument values."""

from __future__ import annotations

import textwrap
from typing import Any, Mapping

from gemini_live_2.catalogs.assets import AssetCatalog
from gemini_live_2.widgets import StageMapPolicy, WidgetRegistry

from .contracts import ComponentChild, ComponentNode, SurfaceDocument


def surface_document_client_payload(
    document: SurfaceDocument, *, asset_urls: Mapping[str, str]
) -> dict[str, Any]:
    """Return the browser-safe view of the active :class:`SurfaceDocument`.

    The document remains the source of component identity, layout, props,
    state and anchors. This envelope adds only URLs for assets that a visible
    component is allowed to render. A hidden component never exposes its
    props, children or non-visibility state to the browser.
    """

    used_asset_ids = {
        asset_id
        for component in document.components
        if component.state["visibility"] == "visible"
        for asset_id in _component_asset_ids(component)
    }
    return {
        "ui_type": "surface_document",
        "surface": {
            "surface_id": document.surface_id,
            "domain_id": document.domain_id,
            "revision": document.revision,
            "components": [_client_component(component) for component in document.components],
            "anchors": [
                {
                    "anchor_id": anchor.anchor_id,
                    "component_id": anchor.component_id,
                    "anchor_key": anchor.anchor_key,
                    "allowed_effect_ids": list(anchor.allowed_effect_ids),
                }
                for anchor in document.anchors
            ],
        },
        "assets": [
            {"id": asset_id, "url": asset_urls[asset_id]}
            for asset_id in sorted(used_asset_ids)
            if isinstance(asset_urls.get(asset_id), str) and asset_urls[asset_id]
        ],
    }


def _client_component(component: ComponentNode) -> dict[str, Any]:
    """Redact a component before it crosses the browser boundary."""

    if component.state["visibility"] == "hidden":
        return {
            "id": component.id,
            "type": component.type,
            "layout": component.layout.to_dict(),
            "props": {},
            "state": {"visibility": "hidden"},
        }
    return component.to_dict()


def _component_asset_ids(component: ComponentNode) -> tuple[str, ...]:
    values = _asset_ids_from_value(component.props)
    for child in component.children:
        values.extend(_child_asset_ids(child))
    return tuple(values)


def _child_asset_ids(child: ComponentChild) -> list[str]:
    values = _asset_ids_from_value(child.props)
    for nested_child in child.children:
        values.extend(_child_asset_ids(nested_child))
    return values


def _asset_ids_from_value(value: object) -> list[str]:
    """Find declared asset references without inferring anything from text."""

    if isinstance(value, Mapping):
        values: list[str] = []
        for key, nested_value in value.items():
            if key == "asset_id" and isinstance(nested_value, str):
                values.append(nested_value)
            else:
                values.extend(_asset_ids_from_value(nested_value))
        return values
    if isinstance(value, (list, tuple)):
        return [asset_id for item in value for asset_id in _asset_ids_from_value(item)]
    return []


def render_visual_stage_map(
    document: SurfaceDocument,
    *,
    widget_registry: WidgetRegistry,
    asset_catalog: AssetCatalog,
) -> str:
    """Render a spatial, text-only copy of the user-visible panel.

    This is intentionally not a character-art wireframe.  Borders and dense
    technical labels made the old map harder to read than the UI itself.  Each
    CSS-grid column becomes a fixed text track and every anchor is printed
    directly below the thing it refers to.
    """

    anchors_by_component = _anchors_by_component(document)
    column_width = 8
    row_height = 4
    canvas = _draw_stage_canvas(
        document.components,
        anchors_by_component,
        widget_registry=widget_registry,
        asset_catalog=asset_catalog,
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


def _anchors_by_component(document: SurfaceDocument) -> dict[str, dict[str, str]]:
    bindings: dict[str, dict[str, str]] = {}
    for anchor in document.anchors:
        bindings.setdefault(anchor.component_id, {})[anchor.anchor_key] = anchor.anchor_id
    return bindings


def _draw_stage_canvas(
    components: tuple[ComponentNode, ...],
    anchors_by_component: Mapping[str, Mapping[str, str]],
    *,
    column_width: int,
    row_height: int,
    widget_registry: WidgetRegistry,
    asset_catalog: AssetCatalog,
) -> list[list[str]]:
    """Place compact visible content by its real GridRect, without borders."""

    canvas_width = 16 * column_width
    canvas = [[" "] * canvas_width for _ in range(10 * row_height)]

    for component in sorted(components, key=lambda item: (item.layout.row, item.layout.col, item.id)):
        x = (component.layout.col - 1) * column_width
        y = (component.layout.row - 1) * row_height
        width = component.layout.col_span * column_width
        height = component.layout.row_span * row_height
        _place_region(
            canvas,
            x=x,
            y=y,
            width=width,
            height=height,
            content=_component_region_lines(
                component,
                anchors_by_component.get(component.id, {}),
                width,
                widget_registry=widget_registry,
                asset_catalog=asset_catalog,
            ),
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


def _component_region_lines(
    component: ComponentNode,
    anchors_by_key: Mapping[str, str],
    width: int,
    *,
    widget_registry: WidgetRegistry,
    asset_catalog: AssetCatalog,
) -> list[str]:
    if component.state["visibility"] == "hidden":
        return _wrapped_lines("NỘI DUNG ĐANG ẨN", width) + _anchor_lines(tuple(anchors_by_key.values()), width)
    root_policy = _stage_map_policy(widget_registry, component.type)
    policy = root_policy.for_state(component.state)
    lines = _policy_lines(
        policy=policy,
        props=component.props,
        children=component.children,
        width=width,
        widget_registry=widget_registry,
        asset_catalog=asset_catalog,
        anchors_by_key=anchors_by_key,
    )
    # A state view only selects content.  The compiler-owned anchor belongs to
    # the component policy itself, so it remains stable across a card flip.
    anchor_id = anchors_by_key.get(root_policy.anchor_key or "")
    return lines + _anchor_lines((anchor_id,), width)


def _policy_lines(
    *,
    policy: StageMapPolicy,
    props: Mapping[str, Any],
    children: tuple[ComponentChild, ...],
    width: int,
    widget_registry: WidgetRegistry,
    asset_catalog: AssetCatalog,
    anchors_by_key: Mapping[str, str],
) -> list[str]:
    if policy.children_layout is not None:
        lines: list[str] = []
        for child in children:
            child_policy = _stage_map_policy(widget_registry, child.type)
            lines.extend(_policy_lines(
                policy=child_policy,
                props=child.props,
                children=child.children,
                width=width,
                widget_registry=widget_registry,
                asset_catalog=asset_catalog,
                anchors_by_key={},
            ))
        return lines

    asset_caption = _asset_caption(policy, props, asset_catalog)
    count = _resolve_path(props, policy.count_source)
    if asset_caption is not None and isinstance(count, int):
        return _object_group_policy_lines(policy, asset_caption, count, anchors_by_key, width)
    lines: list[str] = []
    if asset_caption is not None:
        lines.extend(_prefixed_lines(policy.content_label, asset_caption, width))

    text = _resolve_path(props, policy.text_source)
    if text is not None:
        rendered_text = str(text)
        if policy.quote_text:
            rendered_text = f"“{rendered_text}”"
        lines.extend(_prefixed_lines(policy.content_label, rendered_text, width))
    for source in policy.text_sources:
        value = _resolve_path(props, source.text_source)
        if value is None:
            continue
        rendered_value = str(value)
        if source.quote_text:
            rendered_value = f"“{rendered_value}”"
        lines.extend(_prefixed_lines(source.content_label, rendered_value, width))
    return lines


def _stage_map_policy(widget_registry: WidgetRegistry, component_type: str) -> StageMapPolicy:
    policy = widget_registry.get(component_type).stage_map_policy
    if policy is None:
        raise ValueError(f"widget '{component_type}' does not declare a stage map policy.")
    return policy


def _asset_caption(
    policy: StageMapPolicy, props: Mapping[str, Any], asset_catalog: AssetCatalog
) -> str | None:
    asset_id = _resolve_path(props, policy.asset_source)
    if not isinstance(asset_id, str):
        return None
    asset = asset_catalog.get(asset_id)
    value = _resolve_path(asset, policy.asset_text_source)
    return str(value) if value is not None else None


def _resolve_path(root: object, path: str | None) -> object | None:
    if path is None:
        return None
    current = root
    parts = path.split(".")
    if parts and parts[0] == "props":
        parts = parts[1:]
    if parts and parts[0] == "asset":
        parts = parts[1:]
    for part in parts:
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _prefixed_lines(label: str | None, value: str, width: int) -> list[str]:
    text = f"{label}: {value}" if label else value
    return _wrapped_lines(text, width)


def _object_group_policy_lines(
    policy: StageMapPolicy,
    asset_caption: str,
    count: int,
    anchors_by_key: Mapping[str, str],
    width: int,
) -> list[str]:
    lines = _prefixed_lines(policy.content_label, f"{count} × {asset_caption}", width)
    if count <= 0:
        return lines
    item_text = f"ẢNH: {asset_caption}"
    item_anchor_prefix = policy.item_anchor_prefix or ""
    anchor_texts = [
        f"[anchor: {anchors_by_key.get(f'{item_anchor_prefix}{index}', '')}]"
        if anchors_by_key.get(f"{item_anchor_prefix}{index}") else ""
        for index in range(1, count + 1)
    ]
    cell_width = max(len(item_text), *(len(item) for item in anchor_texts), 1) + 2
    items_per_row = max(1, width // cell_width)
    for start in range(0, count, items_per_row):
        visible_items = min(items_per_row, count - start)
        lines.append("".join(item_text.center(cell_width) for _ in range(visible_items)).rstrip())
        lines.append("".join(
            anchor_texts[start + index].center(cell_width) for index in range(visible_items)
        ).rstrip())
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
