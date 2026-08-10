import { svgElement } from "./svg_utils.js";

/** Draw an overlay arrow that points to one semantic presentation target. */
export function drawArrowEffect({ overlay, rect }) {
  const x = rect.x + rect.width / 2;
  const y = rect.y + rect.height / 2;
  const arrow = svgElement("path", {
    class: "lumi-overlay-shape lumi-overlay-draw-arrow", pathLength: "100",
    d: `M ${Math.max(8, x - 100)} ${Math.max(10, y - 68)} L ${x} ${y}`,
  });
  overlay.append(arrow);
  return () => arrow.remove();
}
