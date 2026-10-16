"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { cleanQuote, highlightSegments } from "@/lib/highlight";

interface EvidenceHighlighterProps {
  /** Texte de l'article. */
  text: string;
  /** Extraits verbatim à surligner (ceux du formulaire : réponse du modèle ou ajoutés à la main). */
  quotes: string[] | null | undefined;
  /** Classe Tailwind du `<mark>` (bicolore). */
  markClassName?: string;
  /** Si fourni : un « + » flottant apparaît sur le texte sélectionné et ajoute l'extrait. */
  onAdd?: (quote: string) => void;
}

/**
 * Surligne les extraits exacts dans le texte avec une balise `<mark>`. Sélectionner du texte fait
 * apparaître un petit « + » : un clic l'ajoute aux extraits de preuve de l'annotation.
 */
export function EvidenceHighlighter({
  text,
  quotes,
  markClassName = "bg-yellow-200 text-amber-900 rounded-sm px-0.5",
  onAdd,
}: EvidenceHighlighterProps) {
  const t = useTranslations("annotate");
  const box = useRef<HTMLDivElement>(null);
  const [pick, setPick] = useState<{ quote: string; x: number; y: number } | null>(null);

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
          // mousedown ne doit pas effacer la sélection avant le clic
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
