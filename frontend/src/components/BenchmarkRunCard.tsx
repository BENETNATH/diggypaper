"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { createPrompt, getBenchmark, BenchmarkResult, BenchmarkRun, ModelInfo, SearchInfo, isScorer } from "@/lib/api";
import { pct, primaryParts, rankResults, resultKey, savedPromptVersion } from "@/lib/benchmark";
import { withSearch } from "@/lib/search";
import { BenchmarkDisagreements } from "@/components/BenchmarkDisagreements";
import { ValidationView } from "@/components/ValidationView";
import { Button } from "@/components/ui/Button";
import { Badge, Card, statusColor } from "@/components/ui/Card";
import { ErrorBanner, ProgressBar, Skeleton } from "@/components/ui/Feedback";

const th = "px-2 py-1 text-right font-normal";
const td = "px-2 py-1.5 text-right tabular-nums";

/** Carte d'un benchmark : en-tête, progression, puis (au dépliage) classement, détails et désaccords. */
export function BenchmarkRunCard({
  projectId,
  run,
  search,
  models,
  analysisSearchId,
  onStop,
}: {
  projectId: string;
  run: BenchmarkRun;
  search?: SearchInfo;
  models: ModelInfo[];
  /** Recherche à analyser ensuite (la recherche courante, pas forcément le sous-lot du benchmark). */
  analysisSearchId?: string;
  onStop: (runId: string) => void;
}) {
  const t = useTranslations("benchmarks");
  const locale = useLocale();
  const router = useRouter();
  const active = run.status === "PENDING" || run.status === "RUNNING";
  const fields = search?.fields ?? [];

  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<BenchmarkResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<string | null>(null); // clé de la ligne dépliée
  const [busy, setBusy] = useState<string | null>(null);
  const saved = useRef<Record<string, string>>({}); // prompts déjà enregistrés (évite les doublons)

  // Les résultats ne sont lus qu'au dépliage, et relus quand le statut change (fin du run).
  useEffect(() => {
    if (!open || active) return;
    let stale = false;
    setResults(null);
    setError(null);
    getBenchmark(projectId, run.id)
      .then((r) => !stale && setResults(r.results ?? []))
      .catch((e) => !stale && setError(e instanceof Error ? e.message : "error"));
    return () => {
      stale = true;
    };
  }, [open, active, projectId, run.id, run.status]);

  const ranked = results ? rankResults(results) : [];
  const crit = primaryParts(ranked.find((r) => r.details?.primary)?.details?.primary, fields);
  const withAgreement = ranked.some((r) => r.details?.agreement != null);
  const scorerOf = (name: string) => {
    const m = models.find((x) => x.name === name);
    return m ? isScorer(m) : false;
  };

  /** Identifiant du prompt à utiliser pour analyser : le prompt enregistré, ou la variante libre enregistrée à la volée. */
  async function promptFor(r: BenchmarkResult): Promise<string | undefined> {
    const v = run.variants?.find((x) => x.name === r.variant);
    if (!v) return run.prompt_template_id ?? undefined;
    if (v.prompt_template_id) return v.prompt_template_id;
    if (!v.system_prompt) return run.prompt_template_id ?? undefined;
    const key = `${run.id}:${v.name}`;
    if (!saved.current[key]) {
      const p = await createPrompt(projectId, {
        version_number: savedPromptVersion(run.id, v.name),
        title: v.name,
        system_prompt: v.system_prompt,
        ...(v.user_prompt_schema ? { user_prompt_schema: v.user_prompt_schema } : {}),
      });
      saved.current[key] = p.id;
    }
    return saved.current[key];
  }

  async function openAnalysis(r: BenchmarkResult) {
    setBusy(resultKey(r));
    setError(null);
    try {
      const prompt = await promptFor(r);
      const q = new URLSearchParams({ model: r.model_name });
      if (prompt) q.set("prompt", prompt);
      router.push(withSearch(`/projects/${projectId}/inference?${q.toString()}`, analysisSearchId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
      setBusy(null);
    }
  }

  const copy = (text: string) => void navigator.clipboard?.writeText(text).catch(() => {});

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 text-sm">
          <p className="font-medium text-foreground">
            {search?.name ?? t("initialSearch")}
            {search && (
              <span className="ml-2 font-normal text-muted">
                {search.lot_size} {t("articlesUnit")}
              </span>
            )}
          </p>
          <p className="font-mono text-xs text-muted">{run.models_tested?.join(", ") ?? "—"}</p>
          <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
            <span>{t("variantCount", { n: Math.max(1, run.variants?.length ?? 0) })}</span>
            {run.options?.use_gold && <Badge color="blue">{t("badgeGold")}</Badge>}
            {!!run.options?.gold_examples && <Badge color="gray">{t("badgeExamples", { n: run.options.gold_examples })}</Badge>}
            {run.created_at && <span>{new Date(run.created_at).toLocaleString(locale)}</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {active && (
            <Button size="sm" variant="danger" onClick={() => onStop(run.id)}>
              ■ {t("stop")}
            </Button>
          )}
          <Badge color={statusColor(run.status)}>{run.status}</Badge>
        </div>
      </div>

      {active && (
        <div className="mt-3">
          <ProgressBar
            value={run.progress && run.progress.total ? (run.progress.done / run.progress.total) * 100 : null}
            label={
              run.progress
                ? t("progress", {
                    done: run.progress.done,
                    total: run.progress.total,
                    model: [run.progress.model, run.progress.variant].filter(Boolean).join(" · "),
                  })
                : t("starting")
            }
          />
        </div>
      )}

      {!active && (
        <div className="mt-3">
          <Button type="button" variant="secondary" size="sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? "▾" : "▸"} {t("results")}
          </Button>
        </div>
      )}

      {open && !active && (
        <div className="mt-3 flex flex-col gap-5">
          {error && <ErrorBanner message={error} />}
          {!results && !error && <Skeleton rows={3} height="h-8" />}
          {results && results.length === 0 && <p className="text-sm text-muted">{t("noResults")}</p>}

          {ranked.length > 0 && (
            <div className="flex flex-col gap-2">
              <h4 className="text-sm font-semibold text-foreground">{t("ranking")}</h4>
              <p className="text-xs text-muted">
                {crit
                  ? t("rankedOn", { item: crit.item ?? t("relevanceLabel"), metric: t(`metric_${crit.metric}`) })
                  : withAgreement
                    ? t("rankedOnAgreement")
                    : t("rankedOnTime")}
              </p>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[40rem] text-sm">
                  <thead>
                    <tr className="border-b border-border text-xs text-muted">
                      <th className="px-2 py-1 text-left font-normal">#</th>
                      <th className="px-2 py-1 text-left font-normal">{t("model")}</th>
                      <th className="px-2 py-1 text-left font-normal">{t("variant")}</th>
                      <th className={th}>{t("primary")}</th>
                      <th className={th}>{t("recall")}</th>
                      <th className={th}>{t("precision")}</th>
                      <th className={th}>{t("f1")}</th>
                      <th className={th}>{t("accuracy")}</th>
                      {withAgreement && <th className={th}>{t("agreement")}</th>}
                      <th className={th}>{t("time")}</th>
                      <th className={th}>{t("nScored")}</th>
                      <th className="px-2 py-1" />
                    </tr>
                  </thead>
                  <tbody>
                    {ranked.map((r, i) => {
                      const key = resultKey(r);
                      const scored = r.details?.scored;
                      return (
                        <Fragment key={key}>
                          <tr className="border-b border-border/50">
                            <td className="px-2 py-1.5 text-left text-muted">{i + 1}</td>
                            <td className="px-2 py-1.5 text-left font-medium text-foreground">
                              {r.model_name}
                              {scorerOf(r.model_name) && (
                                <span className="ml-1">
                                  <Badge>{t("scorerBadge")}</Badge>
                                </span>
                              )}
                            </td>
                            <td className="px-2 py-1.5 text-left">{r.variant ?? t("singlePrompt")}</td>
                            <td className={`${td} font-semibold text-foreground`}>{pct(r.details?.primary?.value)}</td>
                            <td className={td}>{pct(r.recall_score)}</td>
                            <td className={td}>{pct(r.precision_score)}</td>
                            <td className={td}>{pct(r.f1_score)}</td>
                            <td className={td}>{pct(r.accuracy)}</td>
                            {withAgreement && <td className={td}>{pct(r.details?.agreement)}</td>}
                            <td className={td}>{r.execution_time_seconds != null ? `${r.execution_time_seconds.toFixed(1)} s` : "—"}</td>
                            <td className={td}>{r.n_scored ?? "—"}</td>
                            <td className="whitespace-nowrap px-2 py-1.5 text-right">
                              {scored && (
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  aria-expanded={detail === key}
                                  onClick={() => setDetail(detail === key ? null : key)}
                                >
                                  {t("details")}
                                </Button>
                              )}
                              <Button type="button" size="sm" loading={busy === key} onClick={() => void openAnalysis(r)}>
                                {t("useForAnalysis")}
                              </Button>
                            </td>
                          </tr>
                          {detail === key && scored && (
                            <tr>
                              <td colSpan={withAgreement ? 12 : 11} className="bg-surface-muted/40 p-3">
                                <ValidationView scored={scored} primary={r.details?.primary} fields={fields} />
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {!ranked.some((r) => r.details?.scored) && <p className="text-xs text-muted">{t("noGoldScores")}</p>}
              <p className="text-xs text-muted">{t("useForAnalysisHint")}</p>
            </div>
          )}

          {!!run.variants?.length && (
            <details className="rounded-lg border border-border p-3">
              <summary className="cursor-pointer text-sm font-semibold text-foreground">{t("promptsCompared")}</summary>
              <div className="mt-3 flex flex-col gap-3">
                {run.variants.map((v) => (
                  <div key={v.name}>
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium text-foreground">{v.name}</p>
                      {v.system_prompt && (
                        <Button type="button" variant="ghost" size="sm" onClick={() => copy(v.system_prompt ?? "")}>
                          {t("copyPrompt")}
                        </Button>
                      )}
                    </div>
                    <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-surface-muted p-2 text-xs text-foreground">
                      {v.system_prompt ?? t("savedPrompt")}
                    </pre>
                  </div>
                ))}
              </div>
            </details>
          )}

          {results && results.length > 0 && (
            <BenchmarkDisagreements projectId={projectId} runId={run.id} searchId={run.search_id} fields={fields} />
          )}
        </div>
      )}
    </Card>
  );
}
