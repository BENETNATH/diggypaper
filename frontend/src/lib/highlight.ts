/**
 * Evidence highlighting logic (EvidenceHighlighter).
 *
 * Finds the occurrences of the `evidence_quotes` in the Methods text and
 * returns a list of segments (text + highlighted) for the `<mark>` render.
 *
 * Matching is normalized (spec 2026-11-23 P-4): the model's quotes and the
 * extracted text (pypdf) drift on line breaks, nbsp and typographic
 * characters — a strict indexOf left whole quotes unhighlighted. The
 * `<mark>` always contains the ORIGINAL text, not the quote.
 *
 * Pure and testable (no DOM).
 */

export interface Segment {
  text: string;
  highlighted: boolean;
  /** Index (or merged indices) of the quote(s) that produced the range. */
  quoteIdx?: number[];
}

interface Range {
  start: number;
  end: number;
  quoteIdx: number[];
}

// U+00A0 (nbsp) is folded to a space by NFKC itself; the rest needs a map.
const CHAR_MAP: Record<string, string> = {
  "\u2018": "'",
  "\u2019": "'",
  "\u201c": '"',
  "\u201d": '"',
  "\u2013": "-",
  "\u2014": "-",
  "\u2026": "...",
};

/**
 * Normalized view of `s`: NFKC (ligatures, nbsp) + CHAR_MAP, whitespace
 * runs collapsed to one space. `idx` maps each normalized char back to its
 * original index (one entry per normalized char).
 */
function normalize(s: string): { norm: string; idx: number[] } {
  let norm = "";
  const idx: number[] = [];
  let space = false;
  for (let i = 0; i < s.length; i++) {
    const mapped = CHAR_MAP[s[i].normalize("NFKC")] ?? s[i].normalize("NFKC");
    for (const ch of mapped) {
      if (/\s/.test(ch)) {
        if (space) continue; // one space per run
        space = true;
        norm += " "; // any whitespace (incl. \n) normalizes to a space
      } else {
        space = false;
        norm += ch;
      }
      idx.push(i);
    }
  }
  return { norm, idx };
}

/**
 * Finds all occurrences of a quote in the text (normalized matching,
 * case-sensitive — the `evidence_quotes` are verbatim excerpts up to
 * typography). Ranges are in original indices.
 */
function findOccurrences(text: string, quote: string): Range[] {
  if (!quote) return [];
  const t = normalize(text);
  const q = normalize(quote);
  if (!q.norm) return [];
  const ranges: Range[] = [];
  let from = 0;
  while (from <= t.norm.length - q.norm.length) {
    const n = t.norm.indexOf(q.norm, from);
    if (n === -1) break;
    ranges.push({ start: t.idx[n], end: t.idx[n + q.norm.length - 1] + 1, quoteIdx: [] });
    from = n + 1; // overlapping occurrences allowed
  }
  return ranges;
}

/**
 * Merges the sorted ranges (overlaps + adjacencies), unioning the quoteIdx.
 */
function mergeRanges(ranges: Range[]): Range[] {
  if (ranges.length === 0) return [];
  const sorted = [...ranges].sort((a, b) => a.start - b.start || a.end - b.end);
  const merged: Range[] = [{ ...sorted[0] }];
  for (let i = 1; i < sorted.length; i++) {
    const last = merged[merged.length - 1];
    const cur = sorted[i];
    if (cur.start <= last.end) {
      last.end = Math.max(last.end, cur.end);
      for (const qi of cur.quoteIdx) if (!last.quoteIdx.includes(qi)) last.quoteIdx.push(qi);
    } else {
      merged.push({ ...cur });
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
  for (const [i, quote] of (quotes ?? []).entries()) {
    for (const r of findOccurrences(text, quote)) allRanges.push({ ...r, quoteIdx: [i] });
  }
  if (allRanges.length === 0) return [{ text, highlighted: false }];
  const merged = mergeRanges(allRanges);
  const segments: Segment[] = [];
  let pos = 0;
  for (const range of merged) {
    if (range.start > pos) {
      segments.push({ text: text.slice(pos, range.start), highlighted: false });
    }
    segments.push({ text: text.slice(range.start, range.end), highlighted: true, quoteIdx: range.quoteIdx });
    pos = range.end;
  }
  if (pos < text.length) {
    segments.push({ text: text.slice(pos), highlighted: false });
  }
  return segments;
}

/** True when the quote matches the text at least once (same normalized matching). */
export function quoteFound(text: string, quote: string): boolean {
  return findOccurrences(text, quote).length > 0;
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
