import { widgetRendererFor } from "./widgets/registry.js?v=text-fit-20260903";

export function renderSurfaceDocument(surface, assets = [], { revealedComponentIds = new Set() } = {}) {
  const grid = document.createElement("main");
  grid.className = "surface-document-grid";
  grid.setAttribute("aria-label", "Nội dung trực quan");

  const assetUrls = new Map(
    assets
      .filter((asset) => typeof asset?.id === "string" && typeof asset?.url === "string")
      .map((asset) => [asset.id, asset.url]),
  );
  const anchorsByComponent = new Map();
  for (const anchor of Array.isArray(surface?.anchors) ? surface.anchors : []) {
    if (
      typeof anchor?.anchor_id !== "string" ||
      typeof anchor?.component_id !== "string" ||
      typeof anchor?.anchor_key !== "string"
    ) continue;
    if (!anchorsByComponent.has(anchor.component_id)) anchorsByComponent.set(anchor.component_id, {});
    anchorsByComponent.get(anchor.component_id)[anchor.anchor_key] = anchor;
  }

  for (const component of Array.isArray(surface?.components) ? surface.components : []) {
    const layout = component?.layout;
    const renderer = widgetRendererFor(component?.type);
    if (!renderer || !validGrid(layout) || typeof component?.id !== "string") continue;

    const materializedComponent = withAssetUrl(component, assetUrls);
    const node = renderer(materializedComponent, {
      anchorsByKey: anchorsByComponent.get(component.id) || {},
      surfaceId: surface?.surface_id || "",
      renderChild: (child) => renderComponentChild(child, assetUrls),
    });
    if (!node) continue;
    node.dataset.componentId = component.id;
    node.dataset.visibility = component.state?.visibility === "hidden" ? "hidden" : "visible";
    if (revealedComponentIds.has(component.id)) node.classList.add("lumi-widget-revealed");
    node.style.gridColumn = `${layout.col} / span ${layout.col_span}`;
    node.style.gridRow = `${layout.row} / span ${layout.row_span}`;
    grid.append(node);
  }
  return grid;
}

function withAssetUrl(component, assetUrls) {
  return { ...component, props: withAssetUrls(component?.props || {}, assetUrls) };
}

function withAssetUrls(value, assetUrls) {
  if (Array.isArray(value)) return value.map((item) => withAssetUrls(item, assetUrls));
  if (!value || typeof value !== "object") return value;
  const copy = Object.fromEntries(Object.entries(value).map(([key, item]) => [key, withAssetUrls(item, assetUrls)]));
  if (typeof copy.asset_id === "string") copy.asset_url = assetUrls.get(copy.asset_id) || "";
  return copy;
}

function renderComponentChild(child, assetUrls) {
  const renderer = widgetRendererFor(child?.type);
  if (!renderer) return null;
  return renderer(withAssetUrl({ ...child, state: { visibility: "visible" } }, assetUrls), {
    anchorsByKey: {},
    renderChild: (nestedChild) => renderComponentChild(nestedChild, assetUrls),
  });
}

function validGrid(grid) {
  return [grid?.col, grid?.row, grid?.col_span, grid?.row_span]
    .every((value) => Number.isInteger(value) && value > 0);
}
