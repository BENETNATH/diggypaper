/**
 * Client API (frontend → backend FastAPI).
 *
 * Les appels sont faits côté client (fetch, same-origin). En prod, nginx proxy
 * /api/v1 vers le backend ; en dev, le rewrite next.config.mjs fait le même.
 * L'auth est un cookie JWT httpOnly posé par le backend (credentials: include).
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

// --- Types partagés ---------------------------------------------------------

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: "ADMIN" | "RESEARCHER";
  language_preference: string;
  data_consent_given: boolean;
  can_create_projects: boolean;
  created_at: string;
}

/** Métrique de classement d'un item (benchmark) : rappel par défaut. */
export type RankMetric = "recall" | "precision" | "f1" | "accuracy";

export interface SchemaField {
  key: string;
  label: string;
  type: "boolean" | "choice" | "text";
  options?: string[];
  /** Critère de classement des modèles / prompts du benchmark (un seul item) et sa métrique. */
  primary?: boolean;
  metric?: RankMetric;
}

export interface Project {
  retention_delete_on?: string | null; // suppression programmée pour inactivité
  id: string;
  title: string;
  description: string | null;
  status: "DRAFT" | "PROCESSING" | "FROZEN" | "COMPLETED";
  created_by: string | null;
  default_language: string;
  text_sections?: string[];
  question?: string;
  fields?: SchemaField[];
  custom_schema?: boolean;
  question_set?: boolean;
  created_at: string | null;
  article_count?: number;
  fulltext_count?: number;
  role_in_project?: "LEAD" | "ANNOTATOR" | null;
}

export interface Article {
  id: string;
  pmid: string | null;
  source?: string | null; // pubmed | crossref | file… ; null : donnée ancienne
  native_id?: string | null; // identifiant dans cette source (PMID, DOI…)
  pmcid: string | null;
  doi: string | null;
  title: string | null;
  abstract: string | null;
  methods_text: string | null;
  text?: string;
  has_fulltext?: boolean;
  origin?: string | null; // pubmed | references | citation
  origin_seeds?: number | null;
  fulltext_source?: string | null;
  fulltext_note?: string | null;
  doi_url?: string | null;
  fulltext_url?: string | null;
  journal?: string | null;
  pub_date?: string | null;
  authors?: string[];
  first_author?: string | null;
  last_author?: string | null;
  pub_types?: string[];
  answers?: { key: string; label: string; value: unknown; confidence: number | null; source?: "human" | "model" }[];
  prediction_status?: "AUTO_ACCEPTED" | "AUTO_REJECTED" | "NEEDS_HUMAN_REVIEW" | null;
  confidence?: number | null;
  annotated?: boolean;
  review?: Review | null;
  original_language: string | null;
  created_at: string | null;
}

/** Dernière relecture humaine d'un article. */
export interface Review {
  by: string | null;
  at: string | null;
  status: "VALIDATED" | "CORRECTED" | "MANUAL" | null;
}

export interface ArticleReview extends Review {
  decision_json: Record<string, unknown> | null;
  notes: string | null;
}

export interface ArticlePrediction {
  id: string;
  article_id: string;
  prompt_template_id: string | null;
  model_used: string;
  /** Identifiant et version du modèle figés à la prédiction (null : données anciennes). */
  model_identifier?: string | null;
  model_version?: string | null;
  decision_json: Record<string, unknown> | null;
  confidence_score: number | null;
  evidence_quotes: string[] | null;
  status: "AUTO_ACCEPTED" | "AUTO_REJECTED" | "NEEDS_HUMAN_REVIEW";
  created_at: string | null;
}

/** Décision : un champ par SchemaField du projet + clés standard. */
export type AnnotationPayload = Record<string, unknown>;

export interface ImportJob {
  id: string;
  task: string;
  kind?: "import" | "fulltext" | "references" | "citations" | "crate";
  source?: string | null; // base interrogée ; null : références, crate, plein texte
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  query: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

export interface GoldArticle {
  id: string;
  project_id?: string | null;
  article_id: string | null;
  article_title: string | null;
  ground_truth_json: Record<string, unknown>;
  search_id?: string | null;
  /** Relecture dont la référence est la copie (null : saisie manuelle ancienne). */
  source?: {
    annotation_id: string;
    by: string | null;
    at: string | null;
    status: "VALIDATED" | "CORRECTED" | "MANUAL" | null;
    blind: boolean;
  } | null;
  added_by?: string | null;
  /** La relecture a changé depuis la copie : « mettre à jour ». */
  stale?: boolean;
}

// --- Recherches -------------------------------------------------------------

export type SearchKind = "standard" | "tuning" | "blind";

/** Une recherche : une question (ses items) posée à un lot d'articles. */
export interface SearchInfo {
  /** Analyse programmée : en attente de la fin de la recherche d'origine, démarrée, ou échouée. */
  schedule_state?: "waiting" | "started" | "failed" | null;
  schedule?: { model: string; prefilter_model?: string | null; instructions?: string | null; error?: string } | null;
  id: string;
  name: string;
  kind: SearchKind;
  is_default: boolean;
  question: string;
  fields: SchemaField[];
  text_sections: string[] | null;
  parent: { id: string; name: string } | null;
  parent_spec: Selection | null;
  created_at: string | null;
  created_by: string | null;
  lot_size: number;
  predicted: number;
  reviewed: number;
  /** Répartition des résultats de l'IA dans le lot ({} pour une recherche à l'aveugle). */
  breakdown: Partial<Record<"AUTO_ACCEPTED" | "AUTO_REJECTED" | "NEEDS_HUMAN_REVIEW" | "NONE", number>>;
  /** Incertains pas encore relus. */
  to_review: number;
  last_job: { status: InferenceJob["status"]; model: string; model_version?: string | null } | null;
}

/** Vue d'ensemble d'un projet : chiffres globaux (articles distincts) + état de chaque recherche. */
export interface ProjectOverview {
  articles: number;
  fulltext: number;
  analysed: number;
  reviewed: number;
  gold: number;
  searches: SearchInfo[];
}
export const getOverview = (projectId: string): Promise<ProjectOverview> =>
  request(`/projects/${projectId}/overview`);

/** Quels articles : critères cumulés sur les résultats d'une recherche (aucun = tout le corpus). */
export interface Selection {
  from_search_id?: string;
  status?: PredictionFilter;
  answers?: Record<string, string | boolean>;
  reviewed?: boolean;
  has_fulltext?: boolean;
  q?: string;
  confidence_min?: number;
  confidence_max?: number;
  ids?: string[];
  sample_n?: number;
  seed?: number;
}

export interface PromptTemplate {
  id: string;
  project_id: string | null;
  version_number: string;
  title: string | null;
  system_prompt: string | null;
  user_prompt_schema: string | null;
  prompt_type: string | null;
  status: "DRAFT" | "VALIDATED" | "ARCHIVED";
}

export interface ModelInfo {
  id: string;
  name: string;
  backend_type: string | null;
  /** OPENAI_API : nom du modèle seul (l'URL ne sort jamais du serveur). */
  model_identifier: string;
  /** OPENAI_API : `scheme://hôte[:port]` ; null pour les autres backends. */
  endpoint?: string | null;
  ram_usage_mb: number | null;
  is_active: boolean | null;
  /** Modèle défini par l'utilisateur courant. */
  mine?: boolean;
  /** Modèle de la plateforme (installé par un administrateur). */
  platform?: boolean;
}

/** Matrice de confusion et métriques de la pertinence ou d'un item booléen. */
export interface Prf {
  tp: number;
  fp: number;
  fn: number;
  tn: number;
  precision: number;
  recall: number;
  f1: number;
  accuracy: number;
  specificity: number;
}

/** Métriques d'un item : booléen (confusion + prf), choix (exactitude), texte (f1 de mots). */
export interface ItemMetrics extends Partial<Prf> {
  type: SchemaField["type"];
  n: number;
}

/** Réponses du modèle comparées au gold (benchmark : details.scored ; analyse : validation). */
export interface Scored {
  n: number;
  relevance: Prf;
  items: Record<string, ItemMetrics>;
  accuracy: number | null;
}

/** Critère de classement : item (ou « relevance ») + métrique + valeur. */
export interface PrimaryCriterion {
  item: string;
  metric: RankMetric;
  value: number | null;
}

export type Validation = Scored & { primary?: PrimaryCriterion };

/** Une formulation à comparer dans un benchmark. */
export interface BenchmarkVariant {
  name: string;
  system_prompt?: string;
  user_prompt_schema?: string;
  prompt_template_id?: string;
}

export interface BenchmarkResult {
  model_name: string;
  variant?: string | null;
  n_scored?: number | null;
  execution_time_seconds: number | null;
  accuracy: number | null;
  precision_score: number | null;
  recall_score: number | null;
  f1_score: number | null;
  details?: {
    articles?: number;
    /** Accord (0..1) avec la majorité des variantes. */
    agreement?: number | null;
    scored?: Scored;
    primary?: PrimaryCriterion;
  } | null;
}

export interface BenchmarkRun {
  id: string;
  project_id: string | null;
  search_id?: string | null;
  prompt_template_id: string | null;
  models_tested: string[] | null;
  variants?: BenchmarkVariant[] | null;
  options?: { use_gold?: boolean; gold_examples?: number } | null;
  progress?: { done: number; total: number; model?: string; variant?: string } | null;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  created_at: string | null;
  results?: BenchmarkResult[];
}

/** Réponse d'un (modèle, variante) pour un article. */
export interface BenchmarkOutput {
  model: string;
  variant: string | null;
  decision: Record<string, unknown> | null;
  error: string | null;
}

export interface BenchmarkArticleRow {
  article_id: string;
  title: string | null;
  gold: Record<string, unknown> | null;
  disagree: boolean;
  outputs: BenchmarkOutput[];
}

/** Instantané d'un modèle figé au démarrage d'une analyse (jamais de clé ni d'URL complète). */
export interface ModelSnapshot {
  name: string;
  backend: string | null;
  identifier: string;
  version: string | null;
  endpoint?: string;
  parameter_size?: string;
  quantization?: string;
  family?: string;
  format?: string;
  size?: number;
  modified_at?: string;
}

export interface InferenceJob {
  id: string;
  project_id: string | null;
  prompt_template_id: string | null;
  model_used: string;
  search_id?: string | null;
  prefilter_model?: string | null;
  /** Instantané des deux étages (null : analyse ancienne). */
  model_info?: { main: ModelSnapshot | null; prefilter: ModelSnapshot | null } | null;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

// --- Client générique -------------------------------------------------------

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
    ...options,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail ?? body.error ?? detail;
    } catch {
      /* pas de body JSON */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

/** Chaîne de requête `?a=1&b=2` sans les valeurs vides (chaîne vide si aucune). */
function qs(params: Record<string, string | boolean | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "" && v !== false) q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

// --- Auth -------------------------------------------------------------------

export const getPublicConfig = (): Promise<{ privacy_url: string | null }> =>
  request("/auth/config");

export async function login(
  email: string,
  password: string,
  consent?: boolean
): Promise<User> {
  const body: Record<string, unknown> = { email, password };
  if (consent !== undefined) body.consent = consent;
  const data = await request<{ user: User }>("/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data.user;
}

export async function register(
  token: string,
  password: string,
  fullName?: string
): Promise<User> {
  return request<User>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ token, password, full_name: fullName ?? null }),
  });
}

export async function me(): Promise<User> {
  return request<User>("/auth/me");
}

/** Déconnexion côté client (le cookie est httpOnly → on ne peut que demander
 *  au serveur de l'effacer ; ici on force un rechargement vers /login). */
export async function logout(): Promise<void> {
  try {
    await request("/auth/logout", { method: "POST" });
  } finally {
    window.location.href = "/login";
  }
}

export async function updateProfile(patch: {
  full_name?: string;
  language_preference?: string;
}): Promise<User> {
  return request<User>("/auth/me", { method: "PATCH", body: JSON.stringify(patch) });
}

export async function changePassword(current_password: string, new_password: string): Promise<void> {
  await request("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });
}

export async function exportMyData(): Promise<unknown> {
  return request("/users/me/export-data");
}

export async function anonymizeMe(): Promise<void> {
  await request("/users/me/anonymize", { method: "DELETE" });
}

// --- Projets ----------------------------------------------------------------

export async function listProjects(): Promise<Project[]> {
  const data = await request<{ items: Project[] }>("/projects");
  return data.items;
}

export async function createProject(payload: {
  title: string;
  description?: string;
  default_language?: string;
}): Promise<Project> {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Le projet ; question, items et parties de texte sont ceux de la recherche `searchId` (déf. initiale). */
export async function getProject(projectId: string, searchId?: string): Promise<Project> {
  return request<Project>(`/projects/${projectId}${qs({ search: searchId })}`);
}

export async function deleteProject(projectId: string): Promise<void> {
  await request<void>(`/projects/${projectId}`, { method: "DELETE" });
}

// --- Import (MODULE 2) ------------------------------------------------------

export async function queryVolume(
  projectId: string,
  query: string
): Promise<{ count: number; max: number }> {
  return request<{ count: number; max: number }>(`/projects/${projectId}/query-volume`, {
    method: "POST",
    body: JSON.stringify({ query }),
  });
}

export async function triggerImport(
  projectId: string,
  query: string
): Promise<{ job_id: string; status: string }> {
  return request<{ job_id: string; status: string }>(
    `/projects/${projectId}/import`,
    { method: "POST", body: JSON.stringify({ query }) }
  );
}

export interface ReferencesPreview {
  format: "list" | "bibtex" | "ris" | "csl" | "csv";
  count: number;
  with_doi: number;
  with_pmid: number;
  title_only: number;
  max: number;
}

/** Liste de références (DOI/PMID, BibTeX, RIS, CSL JSON, CSV) : aperçu (dryRun) ou import. */
export async function importReferences(
  projectId: string,
  content: string,
  dryRun: boolean
): Promise<ReferencesPreview> {
  return request<ReferencesPreview>(`/projects/${projectId}/import-references`, {
    method: "POST",
    body: JSON.stringify({ content, dry_run: dryRun }),
  });
}

export interface CitationPreview {
  selected: number;
  seeds: number;
  candidates: number;
  by_overlap: Record<string, number>;
  max: number;
}
export interface CitationRequest {
  selection: Selection;
  direction: "backward" | "forward" | "both";
  min_overlap: number;
  max_candidates: number;
}

/** Chaînage de citations : estimation (dryRun) ou import des candidats les plus liés aux graines. */
export async function expandByCitations(
  projectId: string,
  body: CitationRequest,
  dryRun: boolean
): Promise<CitationPreview> {
  return request<CitationPreview>(`/projects/${projectId}/citations`, {
    method: "POST",
    body: JSON.stringify({ ...body, dry_run: dryRun }),
  });
}

export interface QueryCheck {
  count?: number;
  translation?: string;
  not_found?: string[];
  too_long?: boolean;
  recall?: { total: number; found: number; missing: string[] };
  error?: string;
}
export interface QueryAssist {
  reply: string;
  query: string | null;
  refused: boolean;
  check: QueryCheck | null;
}

/** Assistant de requête PubMed : un modèle propose une requête, PubMed la vérifie. */
export async function assistQuery(
  projectId: string,
  body: { model: string; messages: { role: "user" | "assistant"; content: string }[]; seeds: string }
): Promise<QueryAssist> {
  return request<QueryAssist>(`/projects/${projectId}/query-assistant`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function completeFulltext(projectId: string): Promise<void> {
  await request(`/projects/${projectId}/fulltext`, { method: "POST" });
}

export async function listJobs(projectId: string): Promise<ImportJob[]> {
  const data = await request<{ jobs: ImportJob[] }>(`/projects/${projectId}/jobs`);
  return data.jobs;
}

export async function updateProject(
  projectId: string,
  patch: {
    text_sections?: string[];
    question?: string;
    fields?: SchemaField[];
  },
  searchId?: string
): Promise<Project> {
  return request<Project>(`/projects/${projectId}${qs({ search: searchId })}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function generatePrompt(projectId: string, searchId?: string): Promise<PromptTemplate> {
  return request<PromptTemplate>(`/projects/${projectId}/prompts/generate${qs({ search: searchId })}`, {
    method: "POST",
  });
}

// --- Articles (MODULE 2/5) --------------------------------------------------

export type PredictionFilter = "AUTO_ACCEPTED" | "AUTO_REJECTED" | "NEEDS_HUMAN_REVIEW" | "NONE" | "TO_REVIEW";

export async function listArticles(
  projectId: string,
  page = 1,
  perPage = 50,
  status?: PredictionFilter,
  sort: "added" | "date_desc" | "date_asc" | "score_desc" = "added",
  answers: Record<string, string | boolean> = {},
  searchId?: string,
  text?: string,
  fulltext?: boolean
): Promise<{
  items: Article[];
  total: number;
  page: number;
  per_page: number;
  stats: Record<string, number>;
}> {
  const active = Object.fromEntries(Object.entries(answers).filter(([, v]) => v !== "" && v != null));
  const q =
    (status ? `&status=${status}` : "") +
    (searchId ? `&search=${searchId}` : "") +
    (text?.trim() ? `&q=${encodeURIComponent(text.trim())}` : "") +
    (fulltext != null ? `&fulltext=${fulltext}` : "") +
    `&sort=${sort}` +
    (Object.keys(active).length ? `&answers=${encodeURIComponent(JSON.stringify(active))}` : "");
  return request(`/projects/${projectId}/articles?page=${page}&per_page=${perPage}${q}`);
}

export async function getArticle(
  projectId: string,
  articleId: string,
  searchId?: string
): Promise<Article> {
  return request<Article>(`/projects/${projectId}/articles/${articleId}${qs({ search: searchId })}`);
}

export async function getPrediction(
  projectId: string,
  articleId: string,
  searchId?: string
): Promise<ArticlePrediction | null> {
  return request<ArticlePrediction | null>(
    `/projects/${projectId}/articles/${articleId}/prediction${qs({ search: searchId })}`
  );
}

export async function getReview(
  projectId: string,
  articleId: string,
  searchId?: string
): Promise<ArticleReview | null> {
  return request<ArticleReview | null>(
    `/projects/${projectId}/articles/${articleId}/annotation${qs({ search: searchId })}`
  );
}

export async function getArticleWithPrediction(
  projectId: string,
  articleId: string,
  searchId?: string
): Promise<{ article: Article; prediction: ArticlePrediction | null; review: ArticleReview | null }> {
  const article = await getArticle(projectId, articleId, searchId);
  const prediction = await getPrediction(projectId, articleId, searchId).catch(() => null);
  const review = await getReview(projectId, articleId, searchId).catch(() => null);
  return { article, prediction, review };
}

/** Dépôt manuel du PDF d'un article : le texte est extrait et remplace le texte intégral. */
export async function uploadArticlePdf(
  projectId: string,
  articleId: string,
  file: File,
  force = false,
  searchId?: string
): Promise<Article & { pdf_check?: "match" | "unverified" | "forced" }> {
  return request(`/projects/${projectId}/articles/${articleId}/fulltext${qs({ force, search: searchId })}`, {
    method: "POST",
    headers: { "Content-Type": "application/pdf" },
    body: file,
  });
}

/** Dépôt en lot : rattache ce PDF à l'article du projet dont il porte le titre / le DOI. */
export async function matchArticlePdf(
  projectId: string,
  file: File
): Promise<{ article_id: string; title: string | null; score: number }> {
  return request(`/projects/${projectId}/fulltext/match`, {
    method: "POST",
    headers: { "Content-Type": "application/pdf" },
    body: file,
  });
}

/** Importe un RO-Crate (zip exporté par Diggypaper) : crée un projet et restaure son contenu. */
export function importCrate(file: File): Promise<{ project_id: string; job_id: string }> {
  return request("/projects/import-crate", {
    method: "POST",
    headers: { "Content-Type": "application/zip" },
    body: file,
  });
}

/** Relit la notice PubMed de l'article puis relance la recherche de son texte intégral. */
export const searchArticleFulltext = (
  projectId: string,
  articleId: string,
  searchId?: string
): Promise<{ found: boolean; source: string | null; article: Article }> =>
  request(`/projects/${projectId}/articles/${articleId}/fulltext/search${qs({ search: searchId })}`, {
    method: "POST",
  });

export async function submitAnnotation(
  projectId: string,
  articleId: string,
  annotation: AnnotationPayload,
  searchId?: string,
  blind = false
): Promise<Review> {
  return request<Review>(`/projects/${projectId}/articles/${articleId}/annotation${qs({ search: searchId, blind })}`, {
    method: "POST",
    body: JSON.stringify(annotation),
  });
}

// --- Gold (MODULE 3) --------------------------------------------------------

export async function listGold(projectId: string, searchId?: string): Promise<GoldArticle[]> {
  const data = await request<{ items: GoldArticle[] }>(`/projects/${projectId}/gold${qs({ search: searchId })}`);
  return data.items;
}

/** Ajoute au gold la dernière relecture des articles donnés (défaut : tous les articles relus). */
export const goldFromReviews = (
  projectId: string,
  opts: { articleIds?: string[]; refresh?: boolean } = {},
  searchId?: string
): Promise<{ added: number; updated: number; skipped: number }> =>
  request(`/projects/${projectId}/gold/from-reviews${qs({ search: searchId })}`, {
    method: "POST",
    body: JSON.stringify({
      ...(opts.articleIds ? { article_ids: opts.articleIds } : {}),
      ...(opts.refresh ? { refresh: true } : {}),
    }),
  });

export async function deleteGold(projectId: string, goldId: string): Promise<void> {
  await request(`/projects/${projectId}/gold/${goldId}`, { method: "DELETE" });
}

// --- Prompts (MODULE 3) -----------------------------------------------------

export async function listPrompts(projectId: string): Promise<PromptTemplate[]> {
  const data = await request<{ items: PromptTemplate[] }>(
    `/projects/${projectId}/prompts`
  );
  return data.items;
}

export async function getPrompt(
  projectId: string,
  promptId: string
): Promise<PromptTemplate> {
  return request<PromptTemplate>(`/projects/${projectId}/prompts/${promptId}`);
}

export async function createPrompt(
  projectId: string,
  payload: {
    version_number: string;
    title?: string;
    system_prompt?: string;
    user_prompt_schema?: string;
    prompt_type?: string;
  }
): Promise<PromptTemplate> {
  return request<PromptTemplate>(`/projects/${projectId}/prompts`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updatePrompt(
  projectId: string,
  promptId: string,
  payload: Partial<{
    version_number: string;
    title: string;
    system_prompt: string;
    user_prompt_schema: string;
    status: string;
  }>
): Promise<PromptTemplate> {
  return request<PromptTemplate>(`/projects/${projectId}/prompts/${promptId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

// --- Modèles (MODULE 3) -----------------------------------------------------

/** Modèles que l'utilisateur peut utiliser (dans ce projet si `projectId`). */
export async function listModels(projectId?: string): Promise<ModelInfo[]> {
  const q = projectId ? `?project_id=${projectId}` : "";
  const data = await request<{ items: ModelInfo[] }>(`/models${q}`);
  return data.items;
}

export interface ModelShare {
  id: string;
  type: "user" | "project";
  label: string;
  email: string | null;
  project_id: string | null;
}

export const listModelShares = async (modelId: string): Promise<ModelShare[]> =>
  (await request<{ items: ModelShare[] }>(`/models/${modelId}/shares`)).items;

export const addModelShare = (modelId: string, target: { email?: string; project_id?: string }): Promise<void> =>
  request(`/models/${modelId}/shares`, { method: "POST", body: JSON.stringify(target) });

export const removeModelShare = (modelId: string, shareId: string): Promise<void> =>
  request(`/models/${modelId}/shares/${shareId}`, { method: "DELETE" });

export async function createModel(payload: {
  name: string;
  model_identifier: string;
  backend_type?: string;
  ram_usage_mb?: number;
}): Promise<ModelInfo> {
  return request<ModelInfo>("/models", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateModel(
  modelId: string,
  payload: Partial<{
    name: string;
    model_identifier: string;
    ram_usage_mb: number;
    is_active: boolean;
  }>
): Promise<ModelInfo> {
  return request<ModelInfo>(`/models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteModel(modelId: string): Promise<void> {
  await request(`/models/${modelId}`, { method: "DELETE" });
}

export async function testInstalledModel(modelId: string): Promise<{ ok: boolean; error?: string }> {
  return request(`/models/${modelId}/test`, { method: "POST" });
}

export interface OllamaModel {
  name: string;
  size: number | null;
  registered: boolean;
}
export interface PullState {
  state: "running" | "done" | "error";
  status: string;
  completed: number;
  total: number;
  error?: string;
}

export interface HfModel {
  id: string;
  downloads: number;
  likes: number;
  decision?: boolean; // modèle de décision (sans chat) : ne s'installe pas via Ollama
}
export interface HfFile {
  file: string;
  quant?: string | null;
  size?: number | null;
  recommended?: boolean;
}

export async function searchHf(q: string): Promise<HfModel[]> {
  return (await request<{ items: HfModel[] }>(`/models/search?q=${encodeURIComponent(q)}`)).items;
}

export async function hfFiles(repo: string): Promise<{ items: HfFile[] }> {
  return request(`/models/hf/files?repo=${encodeURIComponent(repo)}`);
}

export async function installHf(payload: { repo: string; quant?: string }): Promise<void> {
  await request("/models/hf/install", { method: "POST", body: JSON.stringify(payload) });
}

export async function listOllama(): Promise<{
  items: OllamaModel[];
  pulls: Record<string, PullState>;
}> {
  return request("/models/ollama");
}

export async function pullOllama(name: string): Promise<void> {
  await request("/models/ollama/pull", { method: "POST", body: JSON.stringify({ name }) });
}

export interface ExternalModelInput {
  name: string;
  base_url: string;
  model: string;
  api_key?: string;
}

export async function testExternalModel(p: ExternalModelInput): Promise<{ ok: boolean; error?: string }> {
  return request("/models/external/test", { method: "POST", body: JSON.stringify(p) });
}

export async function addExternalModel(p: ExternalModelInput): Promise<void> {
  await request("/models/external", { method: "POST", body: JSON.stringify(p) });
}

export async function registerOllama(name: string): Promise<void> {
  await request("/models/ollama/register", { method: "POST", body: JSON.stringify({ name }) });
}

// --- Benchmarks (MODULE 3) --------------------------------------------------

export interface BenchmarkCreate {
  models: string[];
  search_id?: string;
  variants?: BenchmarkVariant[];
  prompt_template_id?: string;
  use_gold?: boolean;
  gold_examples?: number;
}

export async function triggerBenchmark(
  projectId: string,
  payload: BenchmarkCreate
): Promise<{ run_id: string; status: string; calls?: number }> {
  return request<{ run_id: string; status: string; calls?: number }>(
    `/projects/${projectId}/benchmarks`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export async function listBenchmarks(projectId: string): Promise<BenchmarkRun[]> {
  const data = await request<{ items: BenchmarkRun[] }>(
    `/projects/${projectId}/benchmarks`
  );
  return data.items;
}

export async function getBenchmark(
  projectId: string,
  runId: string
): Promise<BenchmarkRun> {
  return request<BenchmarkRun>(`/projects/${projectId}/benchmarks/${runId}`);
}

/** Grille article × (modèle, variante) d'un benchmark, désaccords d'abord. */
export const listBenchmarkArticles = (
  projectId: string,
  runId: string,
  opts: { disagree?: boolean; page?: number; perPage?: number } = {}
): Promise<{ total: number; page: number; per_page: number; items: BenchmarkArticleRow[] }> =>
  request(
    `/projects/${projectId}/benchmarks/${runId}/articles${qs({
      disagree: opts.disagree,
      page: String(opts.page ?? 1),
      per_page: String(opts.perPage ?? 25),
    })}`
  );

// --- Inférence (MODULE 4) ---------------------------------------------------

export async function triggerInference(
  projectId: string,
  payload: {
    model: string;
    prefilter_model?: string;
    prompt_template_id?: string;
    resume?: boolean;
    search_id?: string;
    gold_examples?: number;
    instructions?: string;
    validate_with_gold?: boolean;
  }
): Promise<{ job_id: string; status: string }> {
  return request<{ job_id: string; status: string }>(
    `/projects/${projectId}/inference`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export const cancelInference = (projectId: string): Promise<unknown> =>
  request(`/projects/${projectId}/inference/cancel`, { method: "POST" });

export const cancelBenchmark = (projectId: string, runId: string): Promise<unknown> =>
  request(`/projects/${projectId}/benchmarks/${runId}/cancel`, { method: "POST" });

/** Dernier job de la recherche ; avec ``active``, le job en cours du projet (toutes recherches). */
export async function getInference(
  projectId: string,
  searchId?: string,
  active = false
): Promise<InferenceJob | null> {
  const r = await request<{ job: InferenceJob | null }>(
    `/projects/${projectId}/inference${qs({ search: searchId, active: active ? "true" : undefined })}`
  );
  return r.job;
}

// --- Settings (MODULE 7, admin) --------------------------------------------

export type SettingsScope = "GLOBAL" | "PROJECT" | "USER";

/** Valeurs venant de l'environnement du serveur (.env) : elles s'appliquent tant que la base est vide. */
export type EnvSettings = Record<string, { set: boolean; value: string | null }>;
export const getEnvSettings = (): Promise<EnvSettings> => request("/admin/settings/env");

export interface DiskStatus {
  total: number;
  used: number;
  free: number;
  percent: number;
  low: boolean;
  alert_percent: number;
  alert_free_gb: number;
}
export interface StorageProject {
  id: string;
  title: string;
  articles: number;
  pdf_bytes: number;
  last_activity: string;
  idle_days: number;
  warned_at: string | null;
  delete_on: string | null;
}
export interface Storage {
  disk: DiskStatus;
  db_bytes: number | null;
  pdf_bytes: number;
  retention_days: number;
  grace_days: number;
  projects: StorageProject[];
}
export const getStorageStatus = (): Promise<DiskStatus> => request("/admin/storage/status");
export const getStorage = (): Promise<Storage> => request("/admin/storage");
/** « Conserver ce projet » : annule la suppression programmée pour inactivité. */
export const keepProject = (projectId: string): Promise<{ kept_at: string }> =>
  request(`/projects/${projectId}/keep`, { method: "POST" });

export async function getSettings(params?: {
  scope?: SettingsScope;
  project_id?: string;
  user_id?: string;
}): Promise<Record<string, Record<string, string | null>>> {
  const qs = new URLSearchParams();
  if (params?.scope) qs.set("scope", params.scope);
  if (params?.project_id) qs.set("project_id", params.project_id);
  if (params?.user_id) qs.set("user_id", params.user_id);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<Record<string, Record<string, string | null>>>(
    `/admin/settings${suffix}`
  );
}

export async function updateSettings(payload: {
  scope: SettingsScope;
  project_id?: string;
  user_id?: string;
  values: Record<string, string>;
}): Promise<Record<string, Record<string, string | null>>> {
  return request<Record<string, Record<string, string | null>>>("/admin/settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function testSmtp(to?: string): Promise<{ sent: boolean; error?: string }> {
  return request<{ sent: boolean; error?: string }>("/admin/settings/smtp/test", {
    method: "POST",
    body: JSON.stringify({ to: to ?? null }),
  });
}

// --- Utilisateur (RGPD) -----------------------------------------------------

export async function anonymize(): Promise<void> {
  await request("/users/me/anonymize", { method: "DELETE" });
}

export async function exportData(): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>("/users/me/export-data");
}


export interface OllamaCapability {
  mode: "docker" | "host" | "off";
  configured: boolean;
  reachable: boolean;
}
export interface ModelRecommendation {
  profile: "small" | "medium" | "large" | "gpu";
  slow_cpu: boolean; // CPU sans AVX2 : inférence 2 à 3 fois plus lente
  models: { tag: string; ram_gb: number; kind: "tiny" | "small" | "moe" | "gpu"; fits: boolean }[];
}
export interface ModelCapabilities {
  ollama: OllamaCapability;
  hardware: { cores: number; ram_gb: number; avx: boolean; avx2: boolean; avx512: boolean; gpu: boolean };
  recommendation: ModelRecommendation;
}
export const getModelCapabilities = (): Promise<ModelCapabilities> => request("/models/capabilities");

// --- Équipe + export ---------------------------------------------------------

export interface Member {
  user_id: string;
  email: string;
  full_name: string | null;
  role: "LEAD" | "ANNOTATOR";
}

export const listMembers = async (projectId: string): Promise<Member[]> =>
  (await request<{ items: Member[] }>(`/projects/${projectId}/members`)).items;

export const inviteMember = (
  projectId: string,
  email: string,
  role: Member["role"],
  language?: string
): Promise<{ status: "added" | "invited"; email_sent: boolean; link: string | null }> =>
  request(`/projects/${projectId}/members/invite`, {
    method: "POST",
    body: JSON.stringify({ email, role, language }),
  });

export const removeMember = (projectId: string, userId: string): Promise<void> =>
  request(`/projects/${projectId}/members/${userId}`, { method: "DELETE" });

/** Lien de téléchargement du classeur Excel (le cookie d'authentification suffit). */
export const exportUrl = (projectId: string, searchId?: string): string =>
  `${API_BASE}/projects/${projectId}/export.xlsx${qs({ search: searchId })}`;

/** Références RIS (Zotero, EndNote, Mendeley…) ou BibTeX, avec les mêmes filtres que la liste. */
export function referencesUrl(
  projectId: string,
  fmt: "ris" | "bib",
  status?: string,
  answers: Record<string, string> = {},
  searchId?: string,
  text?: string,
  fulltext?: boolean
): string {
  const active = Object.fromEntries(Object.entries(answers).filter(([, v]) => v !== ""));
  const q = new URLSearchParams();
  if (searchId) q.set("search", searchId);
  if (fulltext != null) q.set("fulltext", String(fulltext));
  if (text?.trim()) q.set("q", text.trim());
  if (status) q.set("status", status);
  if (Object.keys(active).length) q.set("answers", JSON.stringify(active));
  const qs = q.toString();
  return `${API_BASE}/projects/${projectId}/export.${fmt}${qs ? `?${qs}` : ""}`;
}

export interface CrateExport {
  id: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  result: { done?: number; total?: number; size?: number; articles?: number; pdf?: number } | null;
  error: string | null;
  created_at: string | null;
  download: boolean;
}

export const prismaPdfUrl = (projectId: string, lang: string, searchId?: string): string =>
  `${API_BASE}/projects/${projectId}/prisma.pdf${qs({ lang, search: searchId })}`;

/** Diagramme PRISMA 2020 (SVG) ; ``lang`` = langue des libellés. */
export const prismaUrl = (projectId: string, lang: string, searchId?: string): string =>
  `${API_BASE}/projects/${projectId}/prisma.svg${qs({ lang, search: searchId })}`;

/** RO-Crate (.zip) : tâche de fond (e-mail à la fin) puis téléchargement. */
export const startCrateExport = (projectId: string, searchId?: string): Promise<CrateExport> =>
  request(`/projects/${projectId}/export/crate${qs({ search: searchId })}`, { method: "POST" });
export const getCrateExport = (projectId: string, searchId?: string): Promise<CrateExport | null> =>
  request(`/projects/${projectId}/export/crate${qs({ search: searchId })}`);
export const crateDownloadUrl = (projectId: string, jobId: string): string =>
  `${API_BASE}/projects/${projectId}/export/crate/${jobId}/download`;

// --- Administration des comptes ---------------------------------------------

export interface AdminUser {
  id: string;
  email: string;
  full_name: string | null;
  role: "ADMIN" | "RESEARCHER";
  language_preference: string | null;
  is_active: boolean;
  can_create_projects: boolean;
  anonymized: boolean;
  created_at: string | null;
  projects: number;
  created_projects: number;
  models: number;
  annotations: number;
}

export interface AdminInvitation {
  id: string;
  email: string;
  language: string;
  project: string | null;
  role: string | null;
  invited_by: string | null;
  created_at: string | null;
}

export interface AdminProject {
  id: string;
  title: string;
  created_by: string | null;
  created_at: string | null;
  members: number;
  articles: number;
}

export const listAdminUsers = async (): Promise<AdminUser[]> =>
  (await request<{ items: AdminUser[] }>("/admin/users")).items;

export const patchAdminUser = (
  id: string,
  patch: Partial<Pick<AdminUser, "full_name" | "role" | "language_preference" | "is_active" | "can_create_projects">>
): Promise<void> => request(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

export const deleteAdminUser = (
  id: string,
  deleteTheirProjects: boolean
): Promise<{ deleted_projects: number; orphaned_projects: string[] }> =>
  request(`/admin/users/${id}?delete_their_projects=${deleteTheirProjects}`, { method: "DELETE" });

export const inviteCreator = (
  email: string,
  language: string
): Promise<{ email_sent: boolean; link: string | null }> =>
  request("/auth/invite", {
    method: "POST",
    body: JSON.stringify({ email, language, can_create_projects: true }),
  });

export const purgeOrphanUsers = (): Promise<{ deleted: number }> =>
  request("/admin/users/purge-orphans", { method: "POST" });

export const listAdminInvitations = async (): Promise<AdminInvitation[]> =>
  (await request<{ items: AdminInvitation[] }>("/admin/invitations")).items;

export const resendInvitation = (id: string): Promise<{ email_sent: boolean; link: string | null }> =>
  request(`/admin/invitations/${id}/resend`, { method: "POST" });

export const revokeInvitation = (id: string): Promise<void> =>
  request(`/admin/invitations/${id}`, { method: "DELETE" });

export const listAdminProjects = async (): Promise<AdminProject[]> =>
  (await request<{ items: AdminProject[] }>("/admin/projects")).items;

// --- Recherches (une question posée à un lot d'articles) ---------------------

export const listSearches = async (projectId: string): Promise<SearchInfo[]> =>
  (await request<{ items: SearchInfo[] }>(`/projects/${projectId}/searches`)).items;

/** Nombre d'articles qui répondent à la sélection (avant de créer la recherche). */
export const previewSelection = async (projectId: string, selection: Selection): Promise<number> =>
  (
    await request<{ count: number }>(`/projects/${projectId}/searches/preview`, {
      method: "POST",
      body: JSON.stringify(selection),
    })
  ).count;

export interface SearchCreate {
  name?: string; // vide : AAMMJJ-n
  question?: string;
  fields?: SchemaField[];
  text_sections?: string[];
  kind?: SearchKind;
  selection: Selection;
  /** Analyse programmée : démarre quand la recherche d'origine (selection.from_search_id) a fini. */
  schedule?: { model: string; prefilter_model?: string; instructions?: string };
}

export const createSearch = (projectId: string, payload: SearchCreate): Promise<SearchInfo> =>
  request(`/projects/${projectId}/searches`, { method: "POST", body: JSON.stringify(payload) });

export const renameSearch = (projectId: string, searchId: string, name: string): Promise<SearchInfo> =>
  request(`/projects/${projectId}/searches/${searchId}`, { method: "PATCH", body: JSON.stringify({ name }) });

export const deleteSearch = (projectId: string, searchId: string): Promise<void> =>
  request(`/projects/${projectId}/searches/${searchId}`, { method: "DELETE" });
