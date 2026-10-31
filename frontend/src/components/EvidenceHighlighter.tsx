"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { cleanQuote, highlightSegments } from "@/lib/highlight";

interface EvidenceHighlighterProps {
  /** Text of the article. */
  text: string;
  /** Verbatim excerpts to highlight (the form's: model answer or added by hand). */
  quotes: string[] | null | undefined;
  /** Tailwind class of the `<mark>` (two-color). */
  markClassName?: string;
  /** If provided: a floating "+" appears on the selected text and adds the excerpt. */
  onAdd?: (quote: string) => void;
  /** Excerpt to reveal (clicked in the form): the text scrolls to its highlight.
   *  `n` is a nonce to retrigger the scroll on the same excerpt. */
  focus?: { quote: string; n: number } | null;
}

/**
 * Highlights the exact excerpts in the text with a `<mark>` tag. Selecting text makes
 * a small "+" appear: a click adds it to the annotation's evidence excerpts.
 */
export function EvidenceHighlighter({
  text,
  quotes,
  markClassName = "bg-yellow-200 text-amber-900 rounded-sm px-0.5",
  onAdd,
  focus,
}: EvidenceHighlighterProps) {
  const t = useTranslations("annotate");
  const box = useRef<HTMLDivElement>(null);
  const [pick, setPick] = useState<{ quote: string; x: number; y: number } | null>(null);

  // Click on an excerpt from the form: the document scrolls to its highlight,
  // which blinks for a while (the quote is verbatim, we look for it in
  // the <mark> text; first match if it appears several times).
  // On mobile the document panel appears in the same render (tab): we
  // retry on the next frame until the <mark> has a box (height = 0).
  // The scroll is computed explicitly on the scrollable ancestor (the section):
  // the content div does not scroll, and scrollIntoView({block:"center"}) used
  // to compute relative to the window amid layout reflow, leaving the highlight
  // at the bottom of the panel instead of centering it.
  // While the flash is visible, we re-center if the content reflows
  // (e.g. a text image that finishes loading and pushes the highlight).
  useEffect(() => {
    if (!focus) return;
    let raf = 0;
    let flashTimer = 0;
    let done = false;

    const findScroller = (node: HTMLElement): HTMLElement => {
      let cur: HTMLElement | null = node.parentElement;
      while (cur && cur !== document.body) {
        const oy = getComputedStyle(cur).overflowY;
        if ((oy === "auto" || oy === "scroll") && cur.scrollHeight > cur.clientHeight) {
          return cur;
        }
        cur = cur.parentElement;
      }
      return document.scrollingElement as HTMLElement;
    };

    const center = (el: HTMLElement, target: Element) => {
      const scroller = findScroller(el);
      const boxRect = scroller.getBoundingClientRect();
      const markRect = target.getBoundingClientRect();
      // Centers the mark in the panel, regardless of the page position.
      const offset = markRect.top - boxRect.top + scroller.scrollTop;
      const targetTop = offset - scroller.clientHeight / 2 + markRect.height / 2;
      scroller.scrollTo({ top: targetTop, behavior: "smooth" });
      // The panel itself outside the window (mobile): we bring it into the viewport.
      if (boxRect.top < 0 || boxRect.bottom > window.innerHeight) {
        window.scrollBy({ top: boxRect.top, behavior: "smooth" });
      }
      return offset;
    };

    const tryScroll = () => {
      if (done) return;
      const el = box.current;
      if (!el) return;
      const marks = Array.from(el.querySelectorAll("mark"));
      const target =
        marks.find((m) => m.textContent === focus.quote) ??
        marks.find((m) => (m.textContent ?? "").includes(focus.quote));
      if (!target) return;
      if (target.getBoundingClientRect().height === 0) {
        raf = requestAnimationFrame(tryScroll);
        return;
      }
      done = true;
      let lastOffset = center(el, target);
      target.classList.add("evidence-flash");
      // Re-centering if the mark drifted (reflow: image loading).
      const settle = () => {
        const cur = box.current;
        if (!cur || !target.isConnected || !target.classList.contains("evidence-flash")) return;
        const scroller = findScroller(cur);
        const markRect = target.getBoundingClientRect();
        if (markRect.height === 0) return;
        const offset =
          markRect.top - scroller.getBoundingClientRect().top + scroller.scrollTop;
        if (Math.abs(offset - lastOffset) > 8) {
          lastOffset = offset;
          center(cur, target);
        }
        raf = requestAnimationFrame(settle);
      };
      raf = requestAnimationFrame(settle);
      flashTimer = window.setTimeout(
        () => target.classList.remove("evidence-flash"),
        2200,
      );
    };

    raf = requestAnimationFrame(tryScroll);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(flashTimer);
    };
  }, [focus]);

  useEffect(() => {
    if (!onAdd) return;
    const update = () => {
      const sel = window.getSelection();
      const el = box.current;
      if (!sel || sel.isCollapsed || sel.rangeCount === 0 || !el) return setPick(null);
      const range = sel.getRangeAt(0);
      if (!el.contains(range.commonAncestorContainer)) return setPick(null);
      const quote = cleanQuote(range.toString());
      if (!quote) return setPick(null);
      const r = range.getBoundingClientRect();
      setPick({ quote, x: Math.min(r.right, window.innerWidth - 40), y: Math.max(r.top - 36, 8) });
    };
    const hide = () => setPick(null);
    document.addEventListener("selectionchange", update);
    window.addEventListener("scroll", hide, true);
    return () => {
      document.removeEventListener("selectionchange", update);
      window.removeEventListener("scroll", hide, true);
    };
  }, [onAdd]);

  const segments = highlightSegments(text, quotes);
  return (
    <div ref={box} className="whitespace-pre-wrap leading-relaxed">
      {segments.map((seg, i) =>
        seg.highlighted ? (
          <mark key={i} className={markClassName}>
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        )
      )}
      {onAdd && pick && (
        <button
          type="button"
          // mousedown must not clear the selection before the click
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            onAdd(pick.quote);
            window.getSelection()?.removeAllRanges();
            setPick(null);
          }}
          style={{ position: "fixed", left: pick.x, top: pick.y }}
          className="z-50 flex h-8 w-8 items-center justify-center rounded-full bg-primary text-lg font-bold leading-none text-white shadow-lg hover:opacity-90"
          title={t("addSelection")}
          aria-label={t("addSelection")}
        >
          +
        </button>
      )}
    </div>
  );
}
