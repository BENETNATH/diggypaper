/**
 * API client (frontend → backend FastAPI).
 *
 * Calls are made client-side (fetch, same-origin). In prod, nginx proxies
 * /api/v1 to the backend; in dev, the next.config.mjs rewrite does the same.
 * Auth is an httpOnly JWT cookie set by the backend (credentials: include).
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

// --- Shared types -----------------------------------------------------------

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

/** Ranking metric for an item (benchmark): recall by default. */
export type RankMetric = "recall" | "precision" | "f1" | "accuracy";

export interface SchemaField {
  key: string;
  label: string;
  type: "boolean" | "choice" | "text";
  options?: string[];
  /** Ranking criterion for the benchmark's models / prompts (a single item) and its metric. */
  primary?: boolean;
  metric?: RankMetric;
}

export interface Project {
  retention_delete_on?: string | null; // scheduled deletion due to inactivity
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
  source?: string | null; // pubmed | crossref | file...; null: older data
  native_id?: string | null; // identifier in this source (PMID, DOI...)
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

/** Latest human re-review of an article. */
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
  /** Model ID and version frozen at prediction time (null: older data). */
  model_identifier?: string | null;
  model_version?: string | null;
  decision_json: Record<string, unknown> | null;
  confidence_score: number | null;
  evidence_quotes: string[] | null;
  status: "AUTO_ACCEPTED" | "AUTO_REJECTED" | "NEEDS_HUMAN_REVIEW";
  created_at: string | null;
}

/** Decision: one field per project SchemaField + standard keys. */
export type AnnotationPayload = Record<string, unknown>;

export interface ImportJob {
  id: string;
  task: string;
  kind?: "import" | "fulltext" | "references" | "citations" | "crate";
  source?: string | null; // queried database; null: references, crate, full text
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
  /** Re-review whose reference is the copy (null: older manual entry). */
  source?: {
    annotation_id: string;
    by: string | null;
    at: string | null;
    status: "VALIDATED" | "CORRECTED" | "MANUAL" | null;
    blind: boolean;
  } | null;
  added_by?: string | null;
  /** The re-review changed since the copy: "update". */
  stale?: boolean;
}

// --- Recherches -------------------------------------------------------------

export type SearchKind = "standard" | "tuning" | "blind";

/** A search: a question (its items) posed to a batch of articles. */
export interface SearchInfo {
  /** Scheduled analysis: waiting for the source search to finish, started, or failed. */
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
  /** Distribution of the AI results in the batch ({} for a blind search). */
  breakdown: Partial<Record<"AUTO_ACCEPTED" | "AUTO_REJECTED" | "NEEDS_HUMAN_REVIEW" | "NONE", number>>;
  /** Uncertain, not yet re-reviewed. */
  to_review: number;
  last_job: { status: InferenceJob["status"]; model: string; model_version?: string | null } | null;
}

/** Project overview: global counts (distinct articles) + state of each search. */
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

/** Which articles: cumulative criteria over a search's results (none = the whole corpus). */
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
  /** OPENAI_API: model name only (the URL never leaves the server). */
  model_identifier: string;
  /** OPENAI_API: `scheme://host[:port]`; null for the other backends. */
  endpoint?: string | null;
  ram_usage_mb: number | null;
  is_active: boolean | null;
  /** Model defined by the current user. */
  mine?: boolean;
  /** Platform model (installed by an administrator). */
  platform?: boolean;
  /** Served internally (Ollama/LLaMA.cpp/Transformers, or an API on a private network). */
  internal?: boolean;
}

/** Confusion matrix and metrics for relevance or a boolean item. */
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

/** Metrics for an item: boolean (confusion + prf), choice (accuracy), text (word f1). */
export interface ItemMetrics extends Partial<Prf> {
  type: SchemaField["type"];
  n: number;
}

/** Model answers compared to the gold (benchmark: details.scored; analysis: validation). */
export interface Scored {
  n: number;
  relevance: Prf;
  items: Record<string, ItemMetrics>;
  accuracy: number | null;
}

/** Ranking criterion: item (or "relevance") + metric + value. */
export interface PrimaryCriterion {
  item: string;
  metric: RankMetric;
  value: number | null;
}

export type Validation = Scored & { primary?: PrimaryCriterion };

/** A phrasing to compare in a benchmark. */
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
    /** Agreement (0..1) with the majority of variants. */
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

/** Answer of a (model, variant) for an article. */
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

/** Snapshot of a model frozen at analysis start (never a key or full URL). */
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
  /** Snapshot of both stages (null: older analysis). */
  model_info?: { main: ModelSnapshot | null; prefilter: ModelSnapshot | null } | null;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

// --- Generic client ---------------------------------------------------------

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Map a backend error detail to a known i18n key (``errors.*``) so errors render in the
 * user's language; ``null`` when the detail is unknown (the caller falls back to the raw
 * string). Details are taken from backend/app/api/v1.
 */
export function errorKey(detail: string | undefined): string | null {
  const d = (detail ?? "").trim().toLowerCase();
  if (!d) return "errors.generic";
  if (d === "lead required" || d === "project lead required") return "errors.leadRequired";
  if (d === "question_locked") return "errors.questionLocked";
  if (d === "no_match") return "errors.noMatch";
  if (d === "ambiguous") return "errors.ambiguous";
  if (d.includes("not found")) return "errors.notFound";
  if (d === "conflict") return "errors.conflict";
  return null;
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
      /* no JSON body */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

/** Query string `?a=1&b=2` without empty values (empty string if none). */
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

/** Password reset step 1: always 200 (generic body, no user enumeration). */
export async function requestPasswordReset(email: string): Promise<void> {
  await request("/auth/password-reset/request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

/** Password reset step 2: token from the e-mail link + new password. */
export async function confirmPasswordReset(token: string, newPassword: string): Promise<void> {
  await request("/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}

export async function me(): Promise<User> {
  return request<User>("/auth/me");
}

/** Client-side logout (the cookie is httpOnly, so we can only ask
 *  the server to clear it; here we force a reload to /login). */
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

/** The project; question, items and text parts are those of search `searchId` (default: initial). */
export async function getProject(projectId: string, searchId?: string): Promise<Project> {
  return request<Project>(`/projects/${projectId}${qs({ search: searchId })}`);
}

export async function deleteProject(projectId: string): Promise<void> {
  await request<void>(`/projects/${projectId}`, { method: "DELETE" });
}

// --- Import (MODULE 2) ------------------------------------------------------

export async function queryVolume(
  projectId: string,
  query: string,
  source = "pubmed"
): Promise<{ count: number; max: number }> {
  return request<{ count: number; max: number }>(`/projects/${projectId}/query-volume`, {
    method: "POST",
    body: JSON.stringify({ query, source }),
  });
}

export async function triggerImport(
  projectId: string,
  query: string,
  source = "pubmed"
): Promise<{ job_id: string; status: string }> {
  return request<{ job_id: string; status: string }>(
    `/projects/${projectId}/import`,
    { method: "POST", body: JSON.stringify({ query, source }) }
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

/** List of references (DOI/PMID, BibTeX, RIS, CSL JSON, CSV): preview (dryRun) or import. */
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

/** Citation chaining: estimate (dryRun) or import of the candidates most linked to the seeds. */
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

/** Query assistant: a model proposes a query in the engine's syntax, the source verifies it. */
export async function assistQuery(
  projectId: string,
  body: {
    model: string;
    source?: string;
    messages: { role: "user" | "assistant"; content: string }[];
    seeds: string;
  }
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

/** Manual upload of an article's PDF: the text is extracted and replaces the full text. */
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

/** Batch upload: links this PDF to the project article whose title / DOI it carries. */
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

/** Imports an RO-Crate (zip exported by Diggypaper): creates a project and restores its content. */
export function importCrate(file: File): Promise<{ project_id: string; job_id: string }> {
  return request("/projects/import-crate", {
    method: "POST",
    headers: { "Content-Type": "application/zip" },
    body: file,
  });
}

/** Re-reads the article's PubMed record, then re-runs the search for its full text. */
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

/** Adds to the gold the latest re-review of the given articles (default: all re-reviewed articles). */
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

// --- Models (MODULE 3) ------------------------------------------------------

/** Models the user can use (in this project if `projectId`). */
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
  decision?: boolean; // decision model (no chat): not installed via Ollama
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

/** Article × (model, variant) grid of a benchmark, disagreements first. */
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

// --- Inference (MODULE 4) ---------------------------------------------------

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

/** Latest job of the search; with ``active``, the project's current job (all searches). */
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

/** Values from the server environment (.env): they apply while the database is empty. */
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
/** "Keep this project": cancels the scheduled deletion due to inactivity. */
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
  slow_cpu: boolean; // CPU without AVX2: inference 2 to 3 times slower
  models: { tag: string; ram_gb: number; kind: "tiny" | "small" | "moe" | "gpu"; fits: boolean }[];
}
export interface ModelCapabilities {
  ollama: OllamaCapability;
  hardware: { cores: number; ram_gb: number; avx: boolean; avx2: boolean; avx512: boolean; gpu: boolean };
  recommendation: ModelRecommendation;
}
export const getModelCapabilities = (): Promise<ModelCapabilities> => request("/models/capabilities");

// --- Team + export ----------------------------------------------------------

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

/** Download link for the Excel workbook (the auth cookie is enough). */
export const exportUrl = (projectId: string, searchId?: string): string =>
  `${API_BASE}/projects/${projectId}/export.xlsx${qs({ search: searchId })}`;

/** RIS references (Zotero, EndNote, Mendeley...) or BibTeX, with the same filters as the list. */
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

/** PRISMA 2020 diagram (SVG); ``lang`` = label language. */
export const prismaUrl = (projectId: string, lang: string, searchId?: string): string =>
  `${API_BASE}/projects/${projectId}/prisma.svg${qs({ lang, search: searchId })}`;

/** RO-Crate (.zip): background task (e-mail at the end) then download. */
export const startCrateExport = (projectId: string, searchId?: string): Promise<CrateExport> =>
  request(`/projects/${projectId}/export/crate${qs({ search: searchId })}`, { method: "POST" });
export const getCrateExport = (projectId: string, searchId?: string): Promise<CrateExport | null> =>
  request(`/projects/${projectId}/export/crate${qs({ search: searchId })}`);
export const crateDownloadUrl = (projectId: string, jobId: string): string =>
  `${API_BASE}/projects/${projectId}/export/crate/${jobId}/download`;

// --- Account administration --------------------------------------------------

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

// --- Searches (a question posed to a batch of articles) ----------------------

export const listSearches = async (projectId: string): Promise<SearchInfo[]> =>
  (await request<{ items: SearchInfo[] }>(`/projects/${projectId}/searches`)).items;

/** Number of articles matching the selection (before creating the search). */
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
  /** Scheduled analysis: starts when the source search (selection.from_search_id) finishes. */
  schedule?: { model: string; prefilter_model?: string; instructions?: string };
}

export const createSearch = (projectId: string, payload: SearchCreate): Promise<SearchInfo> =>
  request(`/projects/${projectId}/searches`, { method: "POST", body: JSON.stringify(payload) });

export const renameSearch = (projectId: string, searchId: string, name: string): Promise<SearchInfo> =>
  request(`/projects/${projectId}/searches/${searchId}`, { method: "PATCH", body: JSON.stringify({ name }) });

export const deleteSearch = (projectId: string, searchId: string): Promise<void> =>
  request(`/projects/${projectId}/searches/${searchId}`, { method: "DELETE" });
