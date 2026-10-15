/**
 * Logique pure des recherches d'un projet (sans React ni réseau : testable).
 *
 * Une recherche = une question posée à un lot d'articles. La recherche courante est portée par
 * `?search=<id>` dans l'URL, sinon par sessionStorage (`diggy:search:{projectId}`), sinon
 * absente (= recherche initiale du projet).
 */
import type { PredictionFilter, SearchInfo, Selection } from "@/lib/api";

export const searchStoreKey = (projectId: string): string => `diggy:search:${projectId}`;
/** Sélection préremplie transmise de la page Articles à la page Recherches. */
export const prefillStoreKey = (projectId: string): string => `diggy:newsearch:${projectId}`;
/** Filtres de la liste d'articles, mémorisés par projet ET par recherche. */
export const articlesStoreKey = (projectId: string, searchId?: string): string =>
  `diggy:articles:${projectId}${searchId ? `:${searchId}` : ""}`;

/** L'URL l'emporte sur la mémoire de l'onglet ; aucun des deux = recherche initiale. */
export function resolveSearchId(
  fromUrl: string | null | undefined,
  stored: string | null | undefined
): string | undefined {
  return fromUrl || stored || undefined;
}

/** Ajoute (ou remplace) `search=` dans une URL interne, sans toucher au fragment. */
export function withSearch(href: string, searchId?: string): string {
  if (!searchId) return href;
  const [rest, hash] = href.split("#");
  const [path, query = ""] = rest.split("?");
  const params = new URLSearchParams(query);
  params.set("search", searchId);
  return `${path}?${params.toString()}${hash ? `#${hash}` : ""}`;
}

/** Recherche demandée, sinon la recherche initiale. */
export function currentSearch(searches: SearchInfo[] | null, searchId?: string): SearchInfo | undefined {
  return searches?.find((s) => (searchId ? s.id === searchId : s.is_default));
}

/**
 * Sélection d'articles : retire les critères vides pour ne garder que ceux qui comptent
 * (sans critère = tout le corpus ; critères cumulés).
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

/** Sélection correspondant aux filtres de la page Articles (statut + réponses) de la recherche courante. */
export function selectionFromFilters(
  filter: PredictionFilter | "ALL" | undefined,
  answers: Record<string, string | boolean>,
  fromSearchId?: string,
  text?: string
): Selection {
  return cleanSelection({
    from_search_id: fromSearchId,
    status: filter && filter !== "ALL" ? filter : undefined,
    answers,
    q: text,
  });
}
