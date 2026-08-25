import { widgetRendererFor } from "./widgets/registry.js?v=number-display-20260824";

export function renderPanelIR(panel, assets = [], { revealedBlockIds = new Set() } = {}) {
  const grid = document.createElement("main");
  grid.className = "panel-ir-grid";
  grid.setAttribute("aria-label", "Nội dung trực quan");

  const assetUrls = new Map(
    assets
      .filter((asset) => typeof asset?.id === "string" && typeof asset?.url === "string")
      .map((asset) => [asset.id, asset.url]),
  );
  const anchorsByBlock = new Map();
  for (const anchor of Array.isArray(panel?.anchors) ? panel.anchors : []) {
    if (
      typeof anchor?.anchor_id !== "string" ||
      typeof anchor?.block_id !== "string" ||
      typeof anchor?.anchor_key !== "string" ||
      typeof anchor?.target_id !== "string"
    ) continue;
    if (!anchorsByBlock.has(anchor.block_id)) anchorsByBlock.set(anchor.block_id, {});
    anchorsByBlock.get(anchor.block_id)[anchor.anchor_key] = anchor;
  }

  for (const block of Array.isArray(panel?.blocks) ? panel.blocks : []) {
    const gridSpec = block?.grid;
    const renderer = widgetRendererFor(block?.widget_id);
    if (!renderer || !validGrid(gridSpec) || typeof block?.id !== "string") continue;

    const props = { ...(block.props || {}) };
    if (typeof props.asset_id === "string") props.asset_url = assetUrls.get(props.asset_id) || "";
    const node = renderer({ ...block, props }, { anchorsByKey: anchorsByBlock.get(block.id) || {} });
    if (!node) continue;
    node.dataset.blockId = block.id;
    node.dataset.visibility = block.visibility === "hidden" ? "hidden" : "visible";
    if (revealedBlockIds.has(block.id)) node.classList.add("lumi-widget-revealed");
    node.style.gridColumn = `${gridSpec.col} / span ${gridSpec.col_span}`;
    node.style.gridRow = `${gridSpec.row} / span ${gridSpec.row_span}`;
    grid.append(node);
  }
  return grid;
}

function validGrid(grid) {
  return [grid?.col, grid?.row, grid?.col_span, grid?.row_span]
    .every((value) => Number.isInteger(value) && value > 0);
}
