export function renderTextWidget(block, { anchorsByKey = {} } = {}) {
  const element = document.createElement("p");
  const role = block.props?.role || "body";
  const content = block.visibility === "hidden" ? "Nội dung đang ẩn" : String(block.props?.content || "");
  element.className = `lumi-widget lumi-widget-text lumi-widget-text-${role}`;
  // A short standalone token (result number, operator, flash-card word) uses
  // its grid cell instead of looking like ordinary body copy.
  if (/^.{1,3}$/u.test(content.trim())) element.classList.add("lumi-widget-text-token");
  if (anchorsByKey.text?.target_id) element.dataset.presentId = anchorsByKey.text.target_id;
  element.textContent = content;
  return element;
}
