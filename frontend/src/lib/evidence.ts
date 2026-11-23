/**
 * Evidence quotes of a model prediction come in two shapes
 * (spec 2026-11-23 P-5): a flat list (legacy) or a dict per field (new).
 * `splitEvidence` normalizes both: `all` feeds the highlighter and the
 * global block, `byField` the per-question bullets.
 */
export interface SplitEvidence {
  byField: Record<string, string[]>;
  all: string[];
}

export function splitEvidence(q: unknown): SplitEvidence {
  if (Array.isArray(q)) {
    return { byField: {}, all: q.filter((x): x is string => typeof x === "string") };
  }
  if (q && typeof q === "object") {
    const byField: Record<string, string[]> = {};
    for (const [k, v] of Object.entries(q)) {
      if (Array.isArray(v)) byField[k] = v.filter((x): x is string => typeof x === "string");
    }
    const all: string[] = [];
    for (const list of Object.values(byField)) for (const s of list) if (!all.includes(s)) all.push(s);
    return { byField, all };
  }
  return { byField: {}, all: [] };
}
