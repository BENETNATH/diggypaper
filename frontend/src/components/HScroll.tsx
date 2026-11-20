"use client";

import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { useTranslations } from "next-intl";

type HScrollProps = {
  /** Additional classes for the scroller (e.g. "rounded-lg"). */
  className?: string;
  /** Scrollable content (table, table + legend). */
  children: ReactNode;
};

/**
 * Horizontal scroll container with a floating synchronized scrollbar
 * (sticky at the bottom of the viewport, opaque + shadow), rendered only
 * when the content overflows. Spec 2026-11-19-hscroll-float, mockup validated 2026-11-19.
 */
export function HScroll({ className, children }: HScrollProps) {
  const t = useTranslations("common");
  const id = useId();
  const scrollerRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const thumbRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const dragging = useRef(false);
  const drag = useRef({ x: 0, left: 0, scale: 1 });

  // Imperative thumb + aria updates (no React re-render on scroll).
  const layout = useCallback(() => {
    const s = scrollerRef.current;
    const bar = barRef.current;
    const track = trackRef.current;
    const thumb = thumbRef.current;
    if (!s || !bar || !track || !thumb) return;
    const max = s.scrollWidth - s.clientWidth;
    const trackW = track.clientWidth;
    const thumbW = Math.max(36, trackW * (s.scrollWidth > 0 ? s.clientWidth / s.scrollWidth : 1));
    const x = max > 0 ? (s.scrollLeft / max) * (trackW - thumbW) : 0;
    thumb.style.width = `${thumbW}px`;
    thumb.style.transform = `translateX(${x}px)`;
    bar.setAttribute("aria-valuemax", String(max));
    bar.setAttribute("aria-valuenow", String(Math.round(s.scrollLeft)));
  }, []);

  useEffect(() => {
    const s = scrollerRef.current;
    if (!s) return;
    const recompute = () => {
      setVisible(s.scrollWidth > s.clientWidth);
      layout();
    };
    let raf: number | null = null;
    const onScroll = () => {
      if (raf !== null) return;
      raf = requestAnimationFrame(() => {
        raf = null;
        layout();
      });
    };
    s.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", recompute);
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(recompute) : null;
    ro?.observe(s);
    recompute();
    return () => {
      s.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", recompute);
      ro?.disconnect();
      if (raf !== null) cancelAnimationFrame(raf);
    };
  }, [layout]);

  // Lay out the thumb once the bar is mounted.
  useEffect(() => {
    if (visible) layout();
  }, [visible, layout]);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    const s = scrollerRef.current;
    const bar = barRef.current;
    const track = trackRef.current;
    const thumb = thumbRef.current;
    if (!s || !bar || !track || !thumb) return;
    const max = s.scrollWidth - s.clientWidth;
    if (max <= 0) return;
    const rect = track.getBoundingClientRect();
    const frac = rect.width > 0 ? Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)) : 0;
    if (e.target !== thumb) s.scrollLeft = frac * max; // jump under the pointer, then drag
    dragging.current = true;
    drag.current = { x: e.clientX, left: s.scrollLeft, scale: track.clientWidth > 0 ? max / track.clientWidth : 1 };
    thumb.style.backgroundColor = "var(--primary)";
    bar.setPointerCapture(e.pointerId);
    e.preventDefault();
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const s = scrollerRef.current;
    if (!s || !dragging.current) return;
    const max = s.scrollWidth - s.clientWidth;
    const { x, left, scale } = drag.current;
    s.scrollLeft = Math.max(0, Math.min(max, left + (e.clientX - x) * scale));
  };

  const onPointerEnd = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    dragging.current = false;
    if (thumbRef.current) thumbRef.current.style.backgroundColor = "";
    barRef.current?.releasePointerCapture(e.pointerId);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const s = scrollerRef.current;
    if (!s) return;
    const max = s.scrollWidth - s.clientWidth;
    const step =
      e.key === "ArrowLeft" ? -40
      : e.key === "ArrowRight" ? 40
      : e.key === "PageUp" ? -s.clientWidth
      : e.key === "PageDown" ? s.clientWidth
      : null;
    if (step !== null) {
      e.preventDefault();
      s.scrollLeft = Math.max(0, Math.min(max, s.scrollLeft + step));
    } else if (e.key === "Home") {
      e.preventDefault();
      s.scrollLeft = 0;
    } else if (e.key === "End") {
      e.preventDefault();
      s.scrollLeft = max;
    }
  };

  return (
    <div>
      <div ref={scrollerRef} id={id} className={`overflow-x-auto ${className ?? ""}`}>
        {children}
      </div>
      {visible && (
        <div
          ref={barRef}
          role="scrollbar"
          aria-orientation="horizontal"
          aria-controls={id}
          aria-valuemin={0}
          aria-valuemax={0}
          aria-valuenow={0}
          aria-label={t("hscrollLabel")}
          tabIndex={0}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerEnd}
          onPointerCancel={onPointerEnd}
          onKeyDown={onKeyDown}
          className="group sticky bottom-11 md:bottom-0 z-30 mx-1 mt-1.5 mb-0.5 cursor-pointer rounded-b-[10px] bg-surface px-1 py-2 shadow-[0_-4px_10px_oklch(from_var(--background)_l_c_h_/_0.35)] select-none touch-none focus-visible:outline-none"
        >
          <div ref={trackRef} className="track relative h-2 rounded-full bg-surface-muted">
            <div
              ref={thumbRef}
              className="thumb hscroll-thumb absolute inset-y-0 left-0 w-9 rounded-full transition-[background-color] duration-fast ease-out group-focus-visible:shadow-[0_0_0_2px_var(--focus)]"
            />
          </div>
        </div>
      )}
    </div>
  );
}
