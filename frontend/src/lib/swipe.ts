/**
 * Swipe gesture detection logic (MODULE 5).
 *
 * Swipe right = Accept, swipe left = Reject.
 * Pure and testable (no DOM).
 */

export type SwipeDirection = "left" | "right" | "none";

/** Minimum horizontal movement (px) to qualify as a swipe. */
export const SWIPE_THRESHOLD = 50;

export interface SwipePoint {
  x: number;
  y: number;
}

/**
 * Determines the swipe direction from the start and end points.
 *
 * - Horizontal if |dx| > |dy| AND |dx| >= threshold.
 * - `right` if dx > 0, `left` if dx < 0, `none` otherwise.
 */
export function detectSwipe(start: SwipePoint, end: SwipePoint, threshold = SWIPE_THRESHOLD): SwipeDirection {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if (Math.abs(dx) < threshold) return "none";
  if (Math.abs(dx) <= Math.abs(dy)) return "none"; // movement dominated by the vertical
  return dx > 0 ? "right" : "left";
}
