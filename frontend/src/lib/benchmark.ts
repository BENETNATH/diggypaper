/**
 * Pure logic of the "fine-tuning" benchmark (no React, no network: testable).
 *
 * A benchmark = same models, same batch of articles, different prompts. Results are
 * ranked on the question's main criterion (item "primary" + metric), otherwise
 * on agreement between variants, otherwise on time.
 */
import type { BenchmarkResult, BenchmarkVariant, PrimaryCriterion, RankMetric, SchemaField } from "@/lib/api";

/** Server call cap (articles × models × variants): beyond it, the server refuses. */
export const MAX_CALLS = 3000;
/** Beyond this batch size, a benchmark gets long (CPU): we warn. */
export const LOT_WARN = 100;
export const MAX_VARIANTS = 10;
export const MAX_GOLD_EXAMPLES = 20;
export const DEFAULT_TUNING_N = 30;
export const RANK_METRICS: RankMetric[] = ["recall", "precision", "f1", "accuracy"];

/** A variant being typed (the user prompt comes from the generated prompt, unedited). */
export interface VariantDraft {
  name: string;
  system_prompt: string;
  user_prompt_schema?: string;
}

/**
 * Number of model calls: articles × sum over models (variants, or 1 for a decision
 * model that ignores the prompt). With no variant typed, a single prompt (generated)
 * is used.
 */
export function estimateCalls(lot: number, models: number, variants: number): number {
  return lot * models * Math.max(1, variants);
}

/** "Variant N" name not in the list. */
export function nextVariantName(prefix: string, names: string[]): string {
  let i = names.length + 1;
  while (names.includes(`${prefix} ${i}`)) i++;
  return `${prefix} ${i}`;
}

export type VariantProblem = "name" | "duplicate" | "prompt" | null;

/** Are the typed variants acceptable by the server (non-empty name and prompt, unique names)? */
export function variantsProblem(drafts: VariantDraft[]): VariantProblem {
  const names = drafts.map((d) => d.name.trim());
  if (names.some((n) => !n)) return "name";
  if (new Set(names).size !== names.length) return "duplicate";
  if (drafts.some((d) => !d.system_prompt.trim())) return "prompt";
  return null;
}

/** API `variants` payload (empty = server-generated prompt). */
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
  if (x === null) return 1; // no value: last
  if (y === null) return -1;
  return y - x;
};

const asc = (a: number | null | undefined, b: number | null | undefined): number =>
  a == null || b == null ? desc(a, b) : a - b;

/** Ranking of results: main criterion ↓, then agreement between variants ↓, then time ↑. */
export function rankResults(results: BenchmarkResult[]): BenchmarkResult[] {
  return [...results].sort(
    (a, b) =>
      desc(a.details?.primary?.value, b.details?.primary?.value) ||
      desc(a.details?.agreement, b.details?.agreement) ||
      asc(a.execution_time_seconds, b.execution_time_seconds)
  );
}

/** Criterion label: item (question label if known) + metric. */
export function primaryParts(p: PrimaryCriterion | undefined, fields: SchemaField[]): { item: string | null; metric: RankMetric } | null {
  if (!p) return null;
  const f = fields.find((x) => x.key === p.item);
  return { item: p.item === "relevance" ? null : (f?.label ?? p.item), metric: p.metric };
}

export function resultKey(r: Pick<BenchmarkResult, "model_name" | "variant">): string {
  return `${r.model_name}\u0000${r.variant ?? ""}`;
}

/** Metric value (0..1) as a percentage, "—" if missing. */
export function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)} %`;
}

/** Display value of a decision's item (boolean, choice, text, absent). */
export function showValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "✓" : "✗";
  return String(v);
}

/** Relevance (confidence_score) of a decision, as a percentage; "—" if missing. */
export function relevanceOf(decision: Record<string, unknown> | null | undefined): string {
  const c = decision?.confidence_score;
  return typeof c === "number" ? `${Math.round(c * 100)} %` : "—";
}

/** Version of a prompt saved from a variant (50 chars max server-side). */
export function savedPromptVersion(runId: string, name: string): string {
  return `bench-${runId.slice(0, 8)}-${name}`.slice(0, 50);
}

/** i18n key (benchmarks.err_*) of a known server error message, else null (raw message). */
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

/** Call count announced by the server in "too many calls (N > MAX)", else null. */
export function tooManyCalls(detail: string): number | null {
  const m = /too many calls \((\d+)/.exec(detail);
  return m ? Number(m[1]) : null;
}
