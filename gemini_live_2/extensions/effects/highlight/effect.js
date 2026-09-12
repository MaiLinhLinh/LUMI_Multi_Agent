/** Soft emphasis that applies to the Runtime-validated presentation target. */
export function run({ target }) {
  target.classList.add("lumi-highlight");
  return () => target.classList.remove("lumi-highlight");
}
