export function renderObjectGroupWidget(block, { anchorsByKey = {} } = {}) {
  const container = document.createElement("section");
  container.className = "lumi-widget lumi-widget-object-group";
  if (anchorsByKey.group?.target_id) container.dataset.presentId = anchorsByKey.group.target_id;

  if (block.visibility === "hidden") {
    container.classList.add("lumi-widget-hidden-content");
    const placeholder = document.createElement("p");
    placeholder.className = "lumi-widget-hidden-placeholder";
    placeholder.textContent = "Nội dung đang ẩn";
    container.append(placeholder);
    return container;
  }

  if (block.props?.label) {
    const label = document.createElement("p");
    label.className = "lumi-widget-object-group-label";
    label.textContent = block.props.label;
    container.append(label);
  }

  const items = document.createElement("div");
  items.className = "lumi-widget-object-group-items";
  const count = Number(block.props?.count || 0);
  // The group owns the whole block.  Choose a compact grid from the actual
  // item count so every item receives a proportional share of that space.
  const columns = count <= 1 ? 1 : Math.ceil(Math.sqrt(count));
  const rows = Math.max(1, Math.ceil(count / columns));
  items.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
  items.style.gridTemplateRows = `repeat(${rows}, minmax(0, 1fr))`;
  for (let index = 0; index < count; index += 1) {
    const image = document.createElement("img");
    image.src = block.props?.asset_url || "";
    image.alt = block.props?.label || "";
    image.draggable = false;
    const itemAnchor = anchorsByKey[`item_${index + 1}`];
    if (itemAnchor?.target_id) image.dataset.presentId = itemAnchor.target_id;
    items.append(image);
  }
  container.append(items);
  return container;
}
