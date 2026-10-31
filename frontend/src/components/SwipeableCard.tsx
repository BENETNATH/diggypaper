"use client";

import { ReactNode } from "react";
import { useSwipeable } from "react-swipeable";

interface SwipeableCardProps {
  /** Content of the card. */
  children: ReactNode;
  /** Called when the swipe is to the right (Accept). */
  onSwipeRight?: () => void;
  /** Called when the swipe is to the left (Reject). */
  onSwipeLeft?: () => void;
  /** Swipe threshold (px). */
  threshold?: number;
  /** Tailwind classes of the card. */
  className?: string;
}

/**
 * Swipeable card (MODULE 5): swipe right = Accept, swipe left =
 * Reject. Uses `react-swipeable` (touch gestures + mouse).
 */
export function SwipeableCard({
  children,
  onSwipeRight,
  onSwipeLeft,
  threshold = 50,
  className = "rounded-lg border bg-surface p-4 shadow-sm",
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
