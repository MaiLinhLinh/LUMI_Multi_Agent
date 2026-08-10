/** Reveal result items already rendered but intentionally hidden by a template. */
export function revealItemsEffect({ target }, command) {
  const items = [...target.querySelectorAll(".lumi-item-hidden")];
  if (!items.length) {
    console.warn("[GEMINI_LIVE:UI_REVEAL_ITEMS_EMPTY]", command);
    return undefined;
  }
  items.forEach((item) => item.classList.add("lumi-item-revealed"));
  // Revealed learning objects intentionally persist after the temporary cue.
  return undefined;
}
