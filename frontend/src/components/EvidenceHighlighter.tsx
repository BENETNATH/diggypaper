"use client";

import { highlightSegments } from "@/lib/highlight";

interface EvidenceHighlighterProps {
  /** Texte de la section Methods. */
  text: string;
  /** Extraits verbatim à surligner (evidence_quotes). */
  quotes: string[] | null | undefined;
  /** Classe Tailwind du `<mark>` (bicolore). */
  markClassName?: string;
}

/**
 * Surligne les extraits exacts de `evidence_quotes` dans le texte Methods avec
 * une balise `<mark>` bicolore (MODULE 5).
 */
export function EvidenceHighlighter({
  text,
  quotes,
  markClassName = "bg-yellow-200 text-amber-900 rounded-sm px-0.5",
}: EvidenceHighlighterProps) {
  const segments = highlightSegments(text, quotes);
  return (
    <div className="whitespace-pre-wrap leading-relaxed">
      {segments.map((seg, i) =>
        seg.highlighted ? (
          <mark key={i} className={markClassName}>
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        )
      )}
    </div>
  );
}
