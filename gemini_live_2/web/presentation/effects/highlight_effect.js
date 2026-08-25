/** Soft emphasis that applies to any semantic presentation target. */
export function highlightEffect({ target }) {
  target.classList.add("lumi-highlight");
  return () => target.classList.remove("lumi-highlight");
}
