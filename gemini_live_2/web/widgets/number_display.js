export function renderNumberDisplayWidget(block, { anchorsByKey = {} } = {}) {
  const element = document.createElement("output");
  element.className = "lumi-widget lumi-widget-number-display";
  if (anchorsByKey.number?.anchor_id) element.dataset.anchorId = anchorsByKey.number.anchor_id;
  element.textContent = block.visibility === "hidden" ? "?" : (block.props?.value || "");
  return element;
}
