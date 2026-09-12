import { widgetRendererFor } from "/assets/widgets/registry.js";

export function renderSurfaceDocument(surface, assets = [], {
  revealedComponentIds = new Set(),
  onRuntimeDiagnostic = null,
} = {}) {
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
    const componentId = typeof component?.id === "string" ? component.id : "";
    const anchor = componentId ? firstAnchor(anchorsByComponent.get(componentId) || {}) : null;
    const diagnose = (errorType, observed) => {
      if (!componentId || !anchor?.anchor_id || typeof onRuntimeDiagnostic !== "function") return;
      onRuntimeDiagnostic({
        component_id: componentId,
        anchor_id: anchor.anchor_id,
        error_type: errorType,
        observed,
        repair_scope: "surface_plan",
      });
    };
    if (!componentId || !validGrid(layout)) {
      diagnose("layout_overflow", { status: "invalid_grid" });
      continue;
    }
    if (!renderer) {
      diagnose("renderer_missing", { widget_type: String(component?.type || "") });
      continue;
    }
    if (!validComponentState(component?.state)) {
      diagnose("state_apply_failed", { status: "invalid_state" });
      continue;
    }

    const materializedComponent = withAssetUrl(component, assetUrls);
    let node = null;
    try {
      node = renderer(materializedComponent, {
        anchorsByKey: anchorsByComponent.get(component.id) || {},
        renderChild: (child) => renderComponentChild(child, assetUrls),
        emitInteraction: ({ anchor_id, action } = {}) => {
          if (typeof anchor_id !== "string" || !anchor_id || typeof action !== "string" || !action) {
            diagnose("interaction_emit_failed", { status: "invalid_event" });
            return;
          }
          grid.dispatchEvent(new CustomEvent("panel:interaction", {
            bubbles: true,
            detail: {
              surface_id: surface?.surface_id || "",
              anchor_id,
              action,
            },
          }));
        },
      });
    } catch (error) {
      diagnose("widget_render_failed", { status: "exception", name: String(error?.name || "Error") });
      continue;
    }
    if (!node) continue;
    node.dataset.componentId = component.id;
    node.dataset.visibility = component.state?.visibility === "hidden" ? "hidden" : "visible";
    if (revealedComponentIds.has(component.id)) node.classList.add("lumi-widget-revealed");
    node.style.gridColumn = `${layout.col} / span ${layout.col_span}`;
    node.style.gridRow = `${layout.row} / span ${layout.row_span}`;
    installImageDiagnostics(node, diagnose);
    grid.append(node);
  }
  return grid;
}

function firstAnchor(anchorsByKey) {
  return Object.values(anchorsByKey).find((anchor) => anchor?.anchor_id) || null;
}

function validComponentState(state) {
  return state && (state.visibility === "visible" || state.visibility === "hidden");
}

function installImageDiagnostics(node, diagnose) {
  for (const image of node.querySelectorAll("img")) {
    let retryUsed = false;
    const complete = (status) => image.dispatchEvent(new CustomEvent("lumi:image-final", {
      bubbles: false,
      detail: { status },
    }));
    image.addEventListener("load", () => complete("loaded"));
    image.addEventListener("error", () => {
      if (!retryUsed && image.src) {
        retryUsed = true;
        const failedUrl = image.src;
        image.removeAttribute("src");
        queueMicrotask(() => { image.src = failedUrl; });
        return;
      }
      diagnose("image_load_failed", { status: "error", retry_attempts: 1 });
      complete("failed");
    });
    if (image.complete) queueMicrotask(() => complete(image.naturalWidth > 0 ? "loaded" : "failed"));
  }
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
    emitInteraction: () => {},
  });
}

function validGrid(grid) {
  return [grid?.col, grid?.row, grid?.col_span, grid?.row_span]
    .every((value) => Number.isInteger(value) && value > 0);
}
