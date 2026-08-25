export function renderNumberDisplayWidget(block, { anchorsByKey = {} } = {}) {
  const element = document.createElement("output");
  element.className = "lumi-widget lumi-widget-number-display";
  if (anchorsByKey.number?.target_id) element.dataset.presentId = anchorsByKey.number.target_id;
  element.textContent = block.visibility === "hidden" ? "?" : (block.props?.value || "");
  return element;
}
