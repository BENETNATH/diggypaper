/**
 * Pure logic of a project's searches (no React, no network: testable).
 *
 * A search = a question posed to a batch of articles. The current search is carried by
 * `?search=<id>` in the URL, otherwise by sessionStorage (`diggy:search:{projectId}`), otherwise
 * absent (= the project's initial search).
 */
import type { InferenceJob, PredictionFilter, SearchInfo, Selection } from "@/lib/api";

export const searchStoreKey = (projectId: string): string => `diggy:search:${projectId}`;
/** Prefilled selection passed from the Articles page to the Searches page. */
export const prefillStoreKey = (projectId: string): string => `diggy:newsearch:${projectId}`;
/** Article list filters, remembered per project AND per search. */
export const articlesStoreKey = (projectId: string, searchId?: string): string =>
  `diggy:articles:${projectId}${searchId ? `:${searchId}` : ""}`;

/** The URL wins over the tab memory; neither = initial search. */
export function resolveSearchId(
  fromUrl: string | null | undefined,
  stored: string | null | undefined
): string | undefined {
  return fromUrl || stored || undefined;
}

/** Adds (or replaces) `search=` in an internal URL, without touching the fragment. */
export function withSearch(href: string, searchId?: string): string {
  if (!searchId) return href;
  const [rest, hash] = href.split("#");
  const [path, query = ""] = rest.split("?");
  const params = new URLSearchParams(query);
  params.set("search", searchId);
  return `${path}?${params.toString()}${hash ? `#${hash}` : ""}`;
}

/**
 * Requested search, otherwise the project's primary corpus — the default one when it
 * exists, else the first of the list (the backend sorts default-first, then by date;
 * new projects have no default, R9: the oldest corpus is the primary one).
 */
export function currentSearch(searches: SearchInfo[] | null, searchId?: string): SearchInfo | undefined {
  if (!searches?.length) return undefined;
  return searches.find((s) => (searchId ? s.id === searchId : s.is_default)) ?? searches[0];
}

/**
 * Next free auto-name (YYMMDD-n, UTC date) for a list of existing run names —
 * the client-side prefill of the launch panel (the backend computes the same
 * name when no `name` is sent). Only names of the same day block a rank.
 */
export function nextAutoName(names: Array<string | null | undefined>, now: Date = new Date()): string {
  const iso = now.toISOString();
  const prefix = `${iso.slice(2, 4)}${iso.slice(5, 7)}${iso.slice(8, 10)}`;
  let n = 1;
  while (names.includes(`${prefix}-${n}`)) n += 1;
  return `${prefix}-${n}`;
}

/** i18n status of a run: a run cancelled by the user (FAILED + error=cancelled) reads « Annulée ». */
export function runStatusKey(job: Pick<InferenceJob, "status" | "error">): string {
  if (job.status === "FAILED" && job.error === "cancelled") return "CANCELLED";
  return job.status;
}

/**
 * Article selection: drops empty criteria to keep only the meaningful ones
 * (no criterion = whole corpus; criteria are cumulative).
 */
export function cleanSelection(sel: Selection): Selection {
  const out: Selection = {};
  if (sel.from_search_id) out.from_search_id = sel.from_search_id;
  if (sel.status) out.status = sel.status;
  const answers = Object.fromEntries(Object.entries(sel.answers ?? {}).filter(([, v]) => v !== "" && v != null));
  if (Object.keys(answers).length) out.answers = answers;
  if (sel.reviewed != null) out.reviewed = sel.reviewed;
  if (sel.has_fulltext != null) out.has_fulltext = sel.has_fulltext;
  if (sel.q?.trim()) out.q = sel.q.trim();
  if (sel.confidence_min != null) out.confidence_min = sel.confidence_min;
  if (sel.confidence_max != null) out.confidence_max = sel.confidence_max;
  if (sel.ids?.length) out.ids = sel.ids;
  if (sel.sample_n) {
    out.sample_n = sel.sample_n;
    if (sel.seed != null) out.seed = sel.seed;
  }
  return out;
}

/** Selection matching the current search's Articles page filters (status + answers). */
export function selectionFromFilters(
  filter: PredictionFilter | "ALL" | undefined,
  answers: Record<string, string | boolean>,
  fromSearchId?: string,
  text?: string,
  fulltext?: boolean
): Selection {
  return cleanSelection({
    from_search_id: fromSearchId,
    status: filter && filter !== "ALL" ? filter : undefined,
    answers,
    q: text,
    has_fulltext: fulltext,
  });
}

/** Initial search never used (no analysis, re-review or task): just a container of the
 * corpus, not to be shown as a search until an analysis has been run. */
export const isUntouched = (s: SearchInfo): boolean =>
  s.is_default && s.predicted === 0 && s.reviewed === 0 && !s.last_job;
