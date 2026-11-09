"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  Comparison,
  getComparison,
  getSearchJobs,
  InferenceJob,
} from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Select } from "@/components/ui/Field";
import { ErrorBanner, Skeleton } from "@/components/ui/Feedback";
import { withSearch } from "@/lib/search";

/** The main decision value as text ("—", "true", "oui", "exclude", …). */
const fmt = (v: unknown): string => (v === null || v === undefined ? "—" : String(v));
const pct = (v: number | null): string => (v === null ? "—" : `${(v * 100).toFixed(1)}%`);

/**
 * Comparison of two inference jobs of a search (spec 2026-11-07 §4): two
 * selectors (default: the two most recent completed jobs), the aggregate on
 * top (n, agreement %, models), the article-by-article table, the
 * disagreements at the bottom (link to the article sheet).
 * Remount per search (`key` prop): the panel state resets with the search.
 */
export function ComparisonPanel({
  projectId,
  searchId,
}: {
  projectId: string;
  searchId: string;
}) {
  const t = useTranslations("comparison");
  const [jobs, setJobs] = useState<InferenceJob[] | null>(null);
  const [jobA, setJobA] = useState("");
  const [jobB, setJobB] = useState("");
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  /** Fetches the comparison of a pair (loading state + errors live here, in handlers). */
  const compare = useCallback(
    async (a: string, b: string) => {
      setLoading(true);
      try {
        setData(await getComparison(projectId, searchId, a, b));
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [projectId, searchId]
  );

  // The search's jobs, newest first; the two most recent completed ones are the default pair.
  useEffect(() => {
    let on = true;
    getSearchJobs(projectId, searchId)
      .then((r) => {
        if (!on) return;
        const done = r.jobs.filter((j) => j.status === "COMPLETED");
        setJobs(r.jobs);
        setJobA(done[0]?.id ?? "");
        setJobB(done[1]?.id ?? "");
        if (done.length >= 2) void compare(done[0].id, done[1].id);
      })
      .catch((e: unknown) => on && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      on = false;
    };
  }, [projectId, searchId, compare]);

  const pickA = (v: string) => {
    setJobA(v);
    if (v && v !== jobB) void compare(v, jobB);
    else setData(null);
  };
  const pickB = (v: string) => {
    setJobB(v);
    if (v && v !== jobA) void compare(jobA, v);
    else setData(null);
  };

  if (jobs === null) return <Skeleton rows={3} />;
  if (jobs.length < 2) return null; // a single analysis: nothing to compare yet

  const nameOf = (id: string) => {
    const j = jobs.find((x) => x.id === id);
    return j ? (j.model_used ?? "…") : "…";
  };

  return (
    <Card title={t("title")}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-muted">
          A
          <Select value={jobA} onChange={(e) => pickA(e.target.value)} aria-label="A">
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.model_used} · {new Date(j.created_at ?? 0).toLocaleDateString()}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted">
          B
          <Select value={jobB} onChange={(e) => pickB(e.target.value)} aria-label="B">
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.model_used} · {new Date(j.created_at ?? 0).toLocaleDateString()}
              </option>
            ))}
          </Select>
        </label>
      </div>

      {error && <ErrorBanner message={error} />}

      {!jobA || !jobB ? (
        <p className="text-sm text-muted">{t("pickJobs")}</p>
      ) : jobA === jobB ? (
        <p className="text-sm text-muted">{t("sameJob")}</p>
      ) : loading || !data ? (
        <Skeleton rows={4} />
      ) : (
        <>
          {/* The aggregate: n, agreement %, the two models */}
          <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded-lg bg-surface-muted p-2 text-center">
              <div className="text-lg font-bold text-foreground">{data.n}</div>
              <div className="text-xs text-muted">{t("comparedArticles")}</div>
            </div>
            <div className="rounded-lg bg-surface-muted p-2 text-center">
              <div className="text-lg font-bold text-foreground">{pct(data.agreement)}</div>
              <div className="text-xs text-muted">{t("agreement")}</div>
            </div>
            <div className="rounded-lg bg-surface-muted p-2 text-center">
              <div className="truncate text-sm font-bold text-foreground" title={nameOf(jobA)}>
                {nameOf(jobA)}
              </div>
              <div className="text-xs text-muted">A</div>
            </div>
            <div className="rounded-lg bg-surface-muted p-2 text-center">
              <div className="truncate text-sm font-bold text-foreground" title={nameOf(jobB)}>
                {nameOf(jobB)}
              </div>
              <div className="text-xs text-muted">B</div>
            </div>
          </div>
          {data.n === 0 ? (
            <p className="text-sm text-muted">{t("noCommon")}</p>
          ) : (
            <>
              <div className="overflow-x-auto rounded-lg">
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface-muted text-xs text-muted">
                    <tr>
                      <th className="px-3 py-2">{t("colArticle")}</th>
                      <th className="px-3 py-2">A</th>
                      <th className="px-3 py-2">B</th>
                      <th className="px-3 py-2">{t("colConfidence")}</th>
                      <th className="px-3 py-2">{t("colAgreement")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((r) => (
                      <tr key={r.article_id} className="border-t border-border">
                        <td className="px-3 py-2">
                          <Link
                            href={withSearch(
                              `/projects/${projectId}/annotate?article=${r.article_id}`,
                              searchId
                            )}
                            className="text-primary hover:underline"
                          >
                            {r.title ?? "—"}
                          </Link>
                        </td>
                        <td className="px-3 py-2">{fmt(r.decision_a)}</td>
                        <td className="px-3 py-2">{fmt(r.decision_b)}</td>
                        <td className="px-3 py-2 text-xs text-muted">
                          {r.confidence_a === null ? "—" : r.confidence_a.toFixed(2)} /{" "}
                          {r.confidence_b === null ? "—" : r.confidence_b.toFixed(2)}
                        </td>
                        <td aria-label={r.agree ? "yes" : "no"}>{r.agree ? "✅" : "⚠️"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data.n_excluded > 0 && (
                <p className="mt-2 text-xs text-muted">{t("excluded", { n: data.n_excluded })}</p>
              )}
            </>
          )}
          {/* The disagreements, clickable to the article sheet */}
          {data.disagreements.length > 0 && (
            <div className="mt-3">
              <h3 className="mb-1 text-sm font-semibold text-foreground">
                {t("disagreements", { n: data.disagreements.length })}
              </h3>
              <ul className="flex flex-col gap-1">
                {data.disagreements.map((d) => (
                  <li key={d.article_id} className="text-sm">
                    <Link
                      href={withSearch(
                        `/projects/${projectId}/annotate?article=${d.article_id}`,
                        searchId
                      )}
                      className="text-primary hover:underline"
                    >
                      {d.title ?? "—"}
                    </Link>
                    <span className="ml-2 text-xs text-muted">
                      A: {fmt(d.decision_a)} · B: {fmt(d.decision_b)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
