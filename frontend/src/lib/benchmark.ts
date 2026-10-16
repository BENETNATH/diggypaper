/**
 * Logique pure du benchmark « mise au point » (sans React ni réseau : testable).
 *
 * Un benchmark = mêmes modèles, même lot d'articles, prompts différents. Les résultats sont
 * classés sur le critère principal de la question (item « primary » + métrique), à défaut
 * sur l'accord entre variantes, à défaut sur le temps.
 */
import type { BenchmarkResult, BenchmarkVariant, PrimaryCriterion, RankMetric, SchemaField } from "@/lib/api";

/** Plafond d'appels du serveur (articles × modèles × variantes) : au-delà, il refuse. */
export const MAX_CALLS = 3000;
/** Au-delà de ce lot, un benchmark devient long (CPU) : on prévient. */
export const LOT_WARN = 100;
export const MAX_VARIANTS = 10;
export const MAX_GOLD_EXAMPLES = 20;
export const DEFAULT_TUNING_N = 30;
export const RANK_METRICS: RankMetric[] = ["recall", "precision", "f1", "accuracy"];

/** Une variante en cours de saisie (le prompt utilisateur vient du prompt généré, non édité). */
export interface VariantDraft {
  name: string;
  system_prompt: string;
  user_prompt_schema?: string;
}

/**
 * Nombre d'appels au modèle : articles × somme sur les modèles (variantes, ou 1 pour un modèle de
 * décision qui ignore le prompt). Sans variante saisie, un seul prompt (généré) est utilisé.
 */
export function estimateCalls(lot: number, models: number, variants: number): number {
  return lot * models * Math.max(1, variants);
}

/** Nom « Variante N » absent de la liste. */
export function nextVariantName(prefix: string, names: string[]): string {
  let i = names.length + 1;
  while (names.includes(`${prefix} ${i}`)) i++;
  return `${prefix} ${i}`;
}

export type VariantProblem = "name" | "duplicate" | "prompt" | null;

/** Les variantes saisies sont-elles acceptables par le serveur (nom et prompt non vides, noms uniques) ? */
export function variantsProblem(drafts: VariantDraft[]): VariantProblem {
  const names = drafts.map((d) => d.name.trim());
  if (names.some((n) => !n)) return "name";
  if (new Set(names).size !== names.length) return "duplicate";
  if (drafts.some((d) => !d.system_prompt.trim())) return "prompt";
  return null;
}

/** Charge utile `variants` de l'API (vide = prompt généré côté serveur). */
export function toVariants(drafts: VariantDraft[]): BenchmarkVariant[] {
  return drafts.map((d) => ({
    name: d.name.trim(),
    system_prompt: d.system_prompt,
    ...(d.user_prompt_schema ? { user_prompt_schema: d.user_prompt_schema } : {}),
  }));
}

const desc = (a: number | null | undefined, b: number | null | undefined): number => {
  const x = a ?? null;
  const y = b ?? null;
  if (x === y) return 0;
  if (x === null) return 1; // sans valeur : en dernier
  if (y === null) return -1;
  return y - x;
};

const asc = (a: number | null | undefined, b: number | null | undefined): number =>
  a == null || b == null ? desc(a, b) : a - b;

/** Classement des résultats : critère principal ↓, puis accord entre variantes ↓, puis temps ↑. */
export function rankResults(results: BenchmarkResult[]): BenchmarkResult[] {
  return [...results].sort(
    (a, b) =>
      desc(a.details?.primary?.value, b.details?.primary?.value) ||
      desc(a.details?.agreement, b.details?.agreement) ||
      asc(a.execution_time_seconds, b.execution_time_seconds)
  );
}

/** Libellé du critère : item (libellé de la question si connu) + métrique. */
export function primaryParts(p: PrimaryCriterion | undefined, fields: SchemaField[]): { item: string | null; metric: RankMetric } | null {
  if (!p) return null;
  const f = fields.find((x) => x.key === p.item);
  return { item: p.item === "relevance" ? null : (f?.label ?? p.item), metric: p.metric };
}

export function resultKey(r: Pick<BenchmarkResult, "model_name" | "variant">): string {
  return `${r.model_name}\u0000${r.variant ?? ""}`;
}

/** Valeur de métrique (0..1) en pourcentage, « — » si absente. */
export function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)} %`;
}

/** Valeur d'un item d'une décision pour l'affichage (booléen, choix, texte, absent). */
export function showValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "✓" : "✗";
  return String(v);
}

/** Pertinence (confidence_score) d'une décision, en pourcentage ; « — » si absente. */
export function relevanceOf(decision: Record<string, unknown> | null | undefined): string {
  const c = decision?.confidence_score;
  return typeof c === "number" ? `${Math.round(c * 100)} %` : "—";
}

/** Version d'un prompt enregistré depuis une variante (50 caractères max côté serveur). */
export function savedPromptVersion(runId: string, name: string): string {
  return `bench-${runId.slice(0, 8)}-${name}`.slice(0, 50);
}

/** Clé i18n (benchmarks.err_*) d'un message d'erreur connu du serveur, sinon null (message brut). */
export function benchmarkErrorKey(detail: string): string | null {
  const known: [string, string][] = [
    ["too many calls", "err_tooMany"],
    ["no gold articles", "err_noGold"],
    ["has no articles", "err_noArticles"],
    ["unknown or duplicate model", "err_models"],
    ["variant names must be unique", "err_variantNames"],
    ["already in progress", "err_inProgress"],
    ["lead required", "err_lead"],
  ];
  return known.find(([m]) => detail.includes(m))?.[1] ?? null;
}

/** Nombre d'appels annoncé par le serveur dans « too many calls (N > MAX) », sinon null. */
export function tooManyCalls(detail: string): number | null {
  const m = /too many calls \((\d+)/.exec(detail);
  return m ? Number(m[1]) : null;
}
