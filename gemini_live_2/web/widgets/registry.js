import { renderTextWidget } from "./text.js?v=text-fit-20260903";
import { renderImageWidget } from "./image.js?v=anchor-id-20260825";
import { renderObjectGroupWidget } from "./object_group.js?v=anchor-id-20260825";
import { renderAnswerWidget } from "./answer.js?v=anchor-id-20260825";
import { renderNumberDisplayWidget } from "./number_display.js?v=anchor-id-20260825";
import { renderChoiceWidget } from "./choice.js?v=choice-anchor-20260825";
import { renderFlashcardWidget } from "./flashcard.js?v=surface-document-sd8";

// Widget modules own their DOM shape; the SurfaceDocument renderer owns grid
// placement and assigns compiler-owned anchors to those DOM regions.
const WIDGET_RENDERERS = new Map([
  ["text", renderTextWidget],
  ["image", renderImageWidget],
  ["object_group", renderObjectGroupWidget],
  ["answer", renderAnswerWidget],
  ["number_display", renderNumberDisplayWidget],
  ["choice", renderChoiceWidget],
  ["flashcard", renderFlashcardWidget],
]);

export function widgetRendererFor(widgetType) {
  return WIDGET_RENDERERS.get(widgetType) || null;
}
