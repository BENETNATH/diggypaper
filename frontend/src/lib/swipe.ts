/**
 * Logique de détection des gestes de swipe (MODULE 5).
 *
 * Glisser à droite = Valider (accept), glisser à gauche = Rejeter (reject).
 * Pure et testable (pas de DOM).
 */

export type SwipeDirection = "left" | "right" | "none";

/** Seuil minimal de déplacement horizontal (px) pour qualifier un swipe. */
export const SWIPE_THRESHOLD = 50;

export interface SwipePoint {
  x: number;
  y: number;
}

/**
 * Détermine la direction du swipe à partir des points de départ et de fin.
 *
 * - Horizontal si |dx| > |dy| ET |dx| >= threshold.
 * - `right` si dx > 0, `left` si dx < 0, `none` sinon.
 */
export function detectSwipe(start: SwipePoint, end: SwipePoint, threshold = SWIPE_THRESHOLD): SwipeDirection {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if (Math.abs(dx) < threshold) return "none";
  if (Math.abs(dx) <= Math.abs(dy)) return "none"; // mouvement dominé par la verticale
  return dx > 0 ? "right" : "left";
}
