import { drawArrowEffect } from "./draw_arrow_effect.js";
import { drawCircleEffect } from "./draw_circle_effect.js";
import { highlightEffect } from "./highlight_effect.js";
import { pulseEffect } from "./pulse_effect.js";
import { revealItemsEffect } from "./reveal_items_effect.js";
import { traceEffect } from "./trace_effect.js";

/**
 * Single extension point for named effects. Add a new effect module and one
 * entry here; AnimationController itself stays closed to effect changes.
 */
const EFFECT_REGISTRY = new Map([
  ["highlight", highlightEffect],
  ["reveal", highlightEffect],
  ["pulse", pulseEffect],
  ["circle", drawCircleEffect],
  ["draw_arrow", drawArrowEffect],
  ["trace_line", traceEffect],
  ["reveal_items", revealItemsEffect],
]);

export function effectHandlerFor(effect) {
  return EFFECT_REGISTRY.get(effect) || null;
}
