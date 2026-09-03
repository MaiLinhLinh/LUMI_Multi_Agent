export function renderAnswerWidget(block, { anchorsByKey = {} } = {}) {
  const element = document.createElement("output");
  element.className = "lumi-widget lumi-widget-answer";
  if (anchorsByKey.answer?.anchor_id) element.dataset.anchorId = anchorsByKey.answer.anchor_id;

  // PS3 will make every widget respect runtime visibility.  Keeping this
  // branch here lets the answer widget already represent its own two states.
  element.textContent = block.visibility === "hidden" ? "?" : (block.props?.value || "");
  return element;
}
