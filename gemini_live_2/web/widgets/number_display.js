export function renderNumberDisplayWidget(component, { anchorsByKey = {} } = {}) {
  const element = document.createElement("output");
  element.className = "lumi-widget lumi-widget-number-display";
  if (anchorsByKey.number?.anchor_id) element.dataset.anchorId = anchorsByKey.number.anchor_id;
  element.textContent = component.state?.visibility === "hidden" ? "?" : (component.props?.value || "");
  return element;
}
