/** Effects for a template region that contains a known sequence of items. */

export function runItemEffect(command, context) {
  if (command.effect !== "reveal_items") return false;

  const items = [...context.target.querySelectorAll(".lumi-item-hidden")];
  if (!items.length) {
    console.warn("[GEMINI_LIVE:UI_REVEAL_ITEMS_EMPTY]", command);
    return true;
  }

  items.forEach((item) => item.classList.add("lumi-item-revealed"));
  return true;
}
