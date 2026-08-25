import { renderTextWidget } from "./text.js?v=text-sizing-20260824";
import { renderImageWidget } from "./image.js?v=image-captionless-20260822";
import { renderObjectGroupWidget } from "./object_group.js?v=object-group-sizing-20260822";
import { renderAnswerWidget } from "./answer.js?v=answer-widget-20260824";
import { renderNumberDisplayWidget } from "./number_display.js?v=number-display-20260824";

// CP6 will call this registry while rendering PanelIR.  Widget modules own
// their DOM shape; the panel renderer owns grid placement and final anchor IDs.
const WIDGET_RENDERERS = new Map([
  ["text", renderTextWidget],
  ["image", renderImageWidget],
  ["object_group", renderObjectGroupWidget],
  ["answer", renderAnswerWidget],
  ["number_display", renderNumberDisplayWidget],
]);

export function widgetRendererFor(widgetType) {
  return WIDGET_RENDERERS.get(widgetType) || null;
}
