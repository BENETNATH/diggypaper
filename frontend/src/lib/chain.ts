import type { SearchInfo } from "./api";

/**
 * Chain order of the standard analyses: each analysis right after its parent
 * (BDF over the parent links), the roots first, then the leftovers.
 * Shared by the overview (chain nodes) and the layout (analysis pills).
 */
export function orderChain(searches: SearchInfo[]): SearchInfo[] {
  const byId = new Map(searches.map((s) => [s.id, s]));
  const children = new Map<string, SearchInfo[]>();
  for (const s of searches) {
    const pid = s.parent?.id;
    if (pid && byId.has(pid)) {
      const arr = children.get(pid) ?? [];
      arr.push(s);
      children.set(pid, arr);
    }
  }
  const roots = searches.filter((s) => !s.parent || !byId.has(s.parent.id));
  const ordered: SearchInfo[] = [];
  const seen = new Set<string>();
  const visit = (s: SearchInfo) => {
    if (seen.has(s.id)) return;
    seen.add(s.id);
    ordered.push(s);
    for (const c of children.get(s.id) ?? []) visit(c);
  };
  for (const r of roots) visit(r);
  for (const s of searches) if (!seen.has(s.id)) visit(s);
  return ordered;
}

/** Depth of each analysis in the chain (roots at 0), for the node indentation. */
export function chainDepths(ordered: SearchInfo[]): Map<string, number> {
  const depths = new Map<string, number>();
  ordered.forEach((s) => {
    const p = s.parent?.id ? depths.get(s.parent.id) : undefined;
    depths.set(s.id, p === undefined ? 0 : p + 1);
  });
  return depths;
}
