export function renderChoiceWidget(block, {
  anchorsByKey = {},
  surfaceId = "",
  renderChild = null,
} = {}) {
  const choice = document.createElement("div");
  choice.className = "lumi-widget lumi-widget-choice";
  choice.setAttribute("role", "button");
  choice.tabIndex = 0;
  choice.setAttribute("aria-label", "Lựa chọn");
  const anchorId = String(anchorsByKey.choice?.anchor_id || "");
  if (anchorId) choice.dataset.anchorId = anchorId;

  const dispatchSelection = () => {
    if (!anchorId || !surfaceId) return;
    choice.dispatchEvent(new CustomEvent("panel:interaction", {
      bubbles: true,
      detail: {
        surface_id: surfaceId,
        anchor_id: anchorId,
        action: "select",
      },
    }));
  };

  choice.addEventListener("click", dispatchSelection);
  choice.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    dispatchSelection();
  });

  const content = document.createElement("div");
  content.className = "lumi-widget-choice-content";
  const children = Array.isArray(block.children) ? block.children : [];
  for (const child of children) {
    const node = renderChild?.(child);
    if (!node) continue;
    node.classList.add("lumi-widget-choice-child");
    content.append(node);
  }
  choice.append(content);
  return choice;
}
