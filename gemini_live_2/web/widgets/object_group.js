export function renderObjectGroupWidget(component, { anchorsByKey = {} } = {}) {
  const container = document.createElement("section");
  container.className = "lumi-widget lumi-widget-object-group";
  if (anchorsByKey.group?.anchor_id) container.dataset.anchorId = anchorsByKey.group.anchor_id;

  if (component.state?.visibility === "hidden") {
    container.classList.add("lumi-widget-hidden-content");
    const placeholder = document.createElement("p");
    placeholder.className = "lumi-widget-hidden-placeholder";
    placeholder.textContent = "Nội dung đang ẩn";
    container.append(placeholder);
    return container;
  }

  if (component.props?.label) {
    const label = document.createElement("p");
    label.className = "lumi-widget-object-group-label";
    label.textContent = component.props.label;
    container.append(label);
  }

  const items = document.createElement("div");
  items.className = "lumi-widget-object-group-items";
  const count = Number(component.props?.count || 0);
  // The group owns the whole block.  Choose a compact grid from the actual
  // item count so every item receives a proportional share of that space.
  const columns = count <= 1 ? 1 : Math.ceil(Math.sqrt(count));
  const rows = Math.max(1, Math.ceil(count / columns));
  items.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
  items.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`;
  for (let index = 0; index < count; index += 1) {
    const image = document.createElement("img");
    image.src = component.props?.asset_url || "";
    image.alt = component.props?.label || "";
    image.draggable = false;
    const itemAnchor = anchorsByKey[`item_${index + 1}`];
    if (itemAnchor?.anchor_id) image.dataset.anchorId = itemAnchor.anchor_id;
    items.append(image);
  }
  container.append(items);
  return container;
}
