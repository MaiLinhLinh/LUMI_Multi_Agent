import { svgElement } from "./svg_utils.js";

/** Draw an overlay circle around one semantic presentation target. */
export function drawCircleEffect({ overlay, rect }) {
  const circle = svgElement("ellipse", {
    class: "lumi-overlay-shape lumi-overlay-draw-circle", pathLength: "100",
    cx: rect.x + rect.width / 2,
    cy: rect.y + rect.height / 2,
    rx: Math.max(14, rect.width / 2 + 8),
    ry: Math.max(14, rect.height / 2 + 8),
  });
  overlay.append(circle);
  return () => circle.remove();
}
