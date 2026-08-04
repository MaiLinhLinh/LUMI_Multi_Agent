/** Effects which apply to any template element with `data-present-id`. */

function svg(tag, attributes) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

export function runCommonEffect(command, context) {
  const { target, overlay, rect } = context;
  if (command.effect === "highlight" || command.effect === "reveal") {
    target.classList.add("lumi-highlight");
    return true;
  }
  if (command.effect === "draw_circle") {
    overlay.append(svg("ellipse", {
      class: "lumi-overlay-shape lumi-overlay-draw-circle", pathLength: "100",
      cx: rect.x + rect.width / 2, cy: rect.y + rect.height / 2,
      rx: Math.max(14, rect.width / 2 + 8), ry: Math.max(14, rect.height / 2 + 8),
    }));
    return true;
  }
  if (command.effect === "draw_arrow") {
    const x = rect.x + rect.width / 2;
    const y = rect.y + rect.height / 2;
    overlay.append(svg("path", {
      class: "lumi-overlay-shape lumi-overlay-draw-arrow", pathLength: "100",
      d: `M ${Math.max(8, x - 100)} ${Math.max(10, y - 68)} L ${x} ${y}`,
    }));
    return true;
  }
  return false;
}
