/**
 * scrollIntoView that respects prefers-reduced-motion (WCAG 2.3.3): smooth only
 * when the user has not asked for less motion. House rule (DESIGN.md — Motion):
 * every JS scrollIntoView goes through this.
 */
export function smoothScrollIntoView(
  el: Element | null | undefined,
  block: ScrollLogicalPosition = "start"
): void {
  if (!el) return;
  const reduce =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block });
}
