export const interactionActions = [];

export function render(component, { anchorsByKey = {} } = {}) {
  const container = document.createElement("section");
  container.className = "lumi-widget lumi-widget-object-group";
  if (anchorsByKey.group?.anchor_id) container.dataset.anchorId = anchorsByKey.group.anchor_id;
  if (component.state?.visibility === "hidden") {
    container.classList.add("lumi-widget-hidden-content");
    const placeholder = document.createElement("p"); placeholder.className = "lumi-widget-hidden-placeholder"; placeholder.textContent = "Nội dung đang ẩn"; container.append(placeholder); return container;
  }
  if (component.props?.label) { const label = document.createElement("p"); label.className = "lumi-widget-object-group-label"; label.textContent = component.props.label; container.append(label); }
  const items = document.createElement("div"); items.className = "lumi-widget-object-group-items";
  const count = Number(component.props?.count || 0), columns = count <= 1 ? 1 : Math.ceil(Math.sqrt(count)), rows = Math.max(1, Math.ceil(count / columns));
  items.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`; items.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`;
  for (let index = 0; index < count; index += 1) { const image = document.createElement("img"); image.src = component.props?.asset_url || ""; image.alt = component.props?.label || ""; image.draggable = false; const anchor = anchorsByKey[`item_${index + 1}`]; if (anchor?.anchor_id) image.dataset.anchorId = anchor.anchor_id; items.append(image); }
  container.append(items); return container;
}
