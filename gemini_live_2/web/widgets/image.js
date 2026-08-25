export function renderImageWidget(block, { anchorsByKey = {} } = {}) {
  const figure = document.createElement("figure");
  figure.className = "lumi-widget lumi-widget-image";
  if (anchorsByKey.image?.target_id) figure.dataset.presentId = anchorsByKey.image.target_id;

  if (block.visibility === "hidden") {
    figure.classList.add("lumi-widget-hidden-content");
    const placeholder = document.createElement("span");
    placeholder.className = "lumi-widget-hidden-placeholder";
    placeholder.textContent = "Nội dung đang ẩn";
    figure.append(placeholder);
    return figure;
  }

  const image = document.createElement("img");
  image.src = block.props?.asset_url || "";
  image.alt = block.props?.label || "";
  image.draggable = false;
  figure.append(image);
  return figure;
}
