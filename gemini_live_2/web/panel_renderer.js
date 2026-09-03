import { widgetRendererFor } from "./widgets/registry.js?v=choice-anchor-20260825";

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
      typeof anchor?.anchor_key !== "string"
    ) continue;
    if (!anchorsByBlock.has(anchor.block_id)) anchorsByBlock.set(anchor.block_id, {});
    anchorsByBlock.get(anchor.block_id)[anchor.anchor_key] = anchor;
  }

  for (const block of Array.isArray(panel?.blocks) ? panel.blocks : []) {
    const gridSpec = block?.grid;
    const renderer = widgetRendererFor(block?.widget_id);
    if (!renderer || !validGrid(gridSpec) || typeof block?.id !== "string") continue;

    const materializedBlock = withAssetUrl(block, assetUrls);
    const node = renderer(materializedBlock, {
      anchorsByKey: anchorsByBlock.get(block.id) || {},
      // A PanelIR panel_id is the Runtime surface identity.  The browser only
      // uses the surface name at the interaction boundary.
      surfaceId: panel?.panel_id || "",
      renderChild: (child) => renderChoiceChild(child, assetUrls),
    });
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

function withAssetUrl(block, assetUrls) {
  const props = { ...(block?.props || {}) };
  if (typeof props.asset_id === "string") props.asset_url = assetUrls.get(props.asset_id) || "";
  return { ...block, props };
}

function renderChoiceChild(child, assetUrls) {
  const renderer = widgetRendererFor(child?.widget_id);
  if (!renderer) return null;
  return renderer(withAssetUrl({ ...child, visibility: "visible" }, assetUrls), { anchorsByKey: {} });
}

function validGrid(grid) {
  return [grid?.col, grid?.row, grid?.col_span, grid?.row_span]
    .every((value) => Number.isInteger(value) && value > 0);
}
