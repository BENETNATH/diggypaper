/**
 * Logique de surlignage des preuves (EvidenceHighlighter).
 *
 * Trouve les occurrences exactes des `evidence_quotes` dans le texte Methods et
 * renvoie une liste de segments (texte + surligné) pour le rendu `<mark>`.
 *
 * Pure et testable (pas de DOM).
 */

export interface Segment {
  text: string;
  highlighted: boolean;
}

interface Range {
  start: number;
  end: number;
}

/**
 * Trouve toutes les occurrences d'une citation dans le texte (matching exact,
 * sensible à la casse — les `evidence_quotes` sont des extraits verbatim).
 */
function findOccurrences(text: string, quote: string): Range[] {
  if (!quote) return [];
  const ranges: Range[] = [];
  let from = 0;
  while (from <= text.length - quote.length) {
    const idx = text.indexOf(quote, from);
    if (idx === -1) break;
    ranges.push({ start: idx, end: idx + quote.length });
    from = idx + 1; // occurrences chevauchantes autorisées
  }
  return ranges;
}

/**
 * Fusionne les plages triées (chevauchements + adjacences).
 */
function mergeRanges(ranges: Range[]): Range[] {
  if (ranges.length === 0) return [];
  const sorted = [...ranges].sort((a, b) => a.start - b.start || a.end - b.end);
  const merged: Range[] = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    const last = merged[merged.length - 1];
    const cur = sorted[i];
    if (cur.start <= last.end) {
      last.end = Math.max(last.end, cur.end);
    } else {
      merged.push(cur);
    }
  }
  return merged;
}

/**
 * Découpe le texte en segments (surlignés / non surlignés) selon les citations.
 *
 * - Citations vides ou absentes → un seul segment non surligné (le texte entier).
 * - Texte vide → un segment vide non surligné.
 */
export function highlightSegments(text: string, quotes: string[] | null | undefined): Segment[] {
  if (!text) return [{ text: "", highlighted: false }];
  const allRanges: Range[] = [];
  for (const quote of quotes ?? []) {
    allRanges.push(...findOccurrences(text, quote));
  }
  if (allRanges.length === 0) return [{ text, highlighted: false }];
  const merged = mergeRanges(allRanges);
  const segments: Segment[] = [];
  let pos = 0;
  for (const range of merged) {
    if (range.start > pos) {
      segments.push({ text: text.slice(pos, range.start), highlighted: false });
    }
    segments.push({ text: text.slice(range.start, range.end), highlighted: true });
    pos = range.end;
  }
  if (pos < text.length) {
    segments.push({ text: text.slice(pos), highlighted: false });
  }
  return segments;
}

/** Texte sélectionné → extrait à ajouter (rogné) ; null s'il est trop court pour être une preuve. */
export function cleanQuote(selected: string, max = 1000): string | null {
  const q = selected.trim().slice(0, max).trim();
  return q.length >= 3 ? q : null;
}

/** Ajoute ``quote`` à la liste sans doublon (ordre conservé). */
export function addQuote(quotes: string[], quote: string): string[] {
  return quotes.includes(quote) ? quotes : [...quotes, quote];
}
