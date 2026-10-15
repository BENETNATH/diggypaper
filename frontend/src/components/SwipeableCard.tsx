"use client";

import { ReactNode } from "react";
import { useSwipeable } from "react-swipeable";

interface SwipeableCardProps {
  /** Contenu de la carte. */
  children: ReactNode;
  /** Appelé quand le swipe est à droite (Valider). */
  onSwipeRight?: () => void;
  /** Appelé quand le swipe est à gauche (Rejeter). */
  onSwipeLeft?: () => void;
  /** Seuil de swipe (px). */
  threshold?: number;
  /** Classes Tailwind de la carte. */
  className?: string;
}

/**
 * Carte swipeable (MODULE 5) : glisser à droite = Valider, glisser à gauche =
 * Rejeter. Utilise `react-swipeable` (gestes tactiles + souris).
 */
export function SwipeableCard({
  children,
  onSwipeRight,
  onSwipeLeft,
  threshold = 50,
  className = "rounded-lg border bg-white p-4 shadow-sm",
}: SwipeableCardProps) {
  const handlers = useSwipeable({
    onSwipedRight: () => onSwipeRight?.(),
    onSwipedLeft: () => onSwipeLeft?.(),
    trackTouch: true,
    trackMouse: true,
    ...({ delta: threshold } as object),
  });

  return (
    <div {...handlers} className={className}>
      {children}
    </div>
  );
}
