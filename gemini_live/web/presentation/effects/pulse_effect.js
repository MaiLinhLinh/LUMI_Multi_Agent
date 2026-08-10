/** Brief pulse that applies to any semantic presentation target. */
export function pulseEffect({ target }) {
  target.classList.add("lumi-pulse");
  return () => target.classList.remove("lumi-pulse");
}
