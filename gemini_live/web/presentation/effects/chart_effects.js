/** Reserved extension point for chart-specific effects. */
export function runChartEffect(_command, _context) {
  // The previous app.js did not implement these effects. Keep behaviour stable.
  return false;
}
