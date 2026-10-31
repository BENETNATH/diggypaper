/**
 * Evidence highlighting logic (EvidenceHighlighter).
 *
 * Finds the exact occurrences of the `evidence_quotes` in the Methods text and
 * returns a list of segments (text + highlighted) for the `<mark>` render.
 *
 * Pure and testable (no DOM).
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
 * Finds all occurrences of a quote in the text (exact matching,
 * case-sensitive — the `evidence_quotes` are verbatim excerpts).
 */
function findOccurrences(text: string, quote: string): Range[] {
  if (!quote) return [];
  const ranges: Range[] = [];
  let from = 0;
  while (from <= text.length - quote.length) {
    const idx = text.indexOf(quote, from);
    if (idx === -1) break;
    ranges.push({ start: idx, end: idx + quote.length });
    from = idx + 1; // overlapping occurrences allowed
  }
  return ranges;
}

/**
 * Merges the sorted ranges (overlaps + adjacencies).
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
 * Splits the text into segments (highlighted / not) according to the quotes.
 *
 * - Empty or missing quotes → a single unhighlighted segment (the whole text).
 * - Empty text → an empty unhighlighted segment.
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

/** Selected text → excerpt to add (trimmed); null if it is too short to be evidence. */
export function cleanQuote(selected: string, max = 1000): string | null {
  const q = selected.trim().slice(0, max).trim();
  return q.length >= 3 ? q : null;
}

/** Adds ``quote`` to the list without duplicates (order preserved). */
export function addQuote(quotes: string[], quote: string): string[] {
  return quotes.includes(quote) ? quotes : [...quotes, quote];
}
