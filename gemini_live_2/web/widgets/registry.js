import { renderTextWidget } from "./text.js?v=anchor-id-20260825";
import { renderImageWidget } from "./image.js?v=anchor-id-20260825";
import { renderObjectGroupWidget } from "./object_group.js?v=anchor-id-20260825";
import { renderAnswerWidget } from "./answer.js?v=anchor-id-20260825";
import { renderNumberDisplayWidget } from "./number_display.js?v=anchor-id-20260825";
import { renderChoiceWidget } from "./choice.js?v=choice-anchor-20260825";

// CP6 will call this registry while rendering PanelIR.  Widget modules own
// their DOM shape; the panel renderer owns grid placement and final anchor IDs.
const WIDGET_RENDERERS = new Map([
  ["text", renderTextWidget],
  ["image", renderImageWidget],
  ["object_group", renderObjectGroupWidget],
  ["answer", renderAnswerWidget],
  ["number_display", renderNumberDisplayWidget],
  ["choice", renderChoiceWidget],
]);

export function widgetRendererFor(widgetType) {
  return WIDGET_RENDERERS.get(widgetType) || null;
}
