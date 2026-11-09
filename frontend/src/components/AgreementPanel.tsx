"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Agreement, getAgreement, ModelInfo } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Select } from "@/components/ui/Field";
import { ErrorBanner, Skeleton } from "@/components/ui/Feedback";
import { withSearch } from "@/lib/search";

const pct = (v: number | null): string => (v === null ? "—" : `${(v * 100).toFixed(1)}%`);
const kappa = (v: number | null): string => (v === null ? "—" : v.toFixed(2));
const fmt = (v: unknown): string => (v === null || v === undefined ? "—" : String(v));

/**
 * Reviewer agreement panel (spec 2026-11-07-accord-reviews §5): the two
 * reviewers (default: the most active pair), an optional model for the
 * AI-human agreement, the human block (n, agreement %, kappa, per-field
 * table), the AI block, the disagreement list (link to the article sheet)
 * and the PRISMA-ready summary text.
 * Remount per search (`key` prop): the panel state resets with the search.
 */
export function AgreementPanel({
  projectId,
  searchId,
  models,
}: {
  projectId: string;
  searchId: string;
  models: ModelInfo[];
}) {
  const t = useTranslations("agreement");
  const [reviewers, setReviewers] = useState<{ id: string; email: string; count: number }[]>(
    []
  );
  const [userA, setUserA] = useState("");
  const [userB, setUserB] = useState("");
  const [model, setModel] = useState("");
  const [data, setData] = useState<Agreement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  /** Fetches the agreement for a pair + optional model (loading/errors in handlers).
      When a/b are empty the server picks the default pair; the selectors then
      sync to the response. */
  const fetchAgreement = useCallback(
    (a: string, b: string, m: string) => {
      setLoading(true);
      getAgreement(projectId, searchId, {
        user_a: a || undefined,
        user_b: b || undefined,
        model: m || undefined,
      })
        .then((r) => {
          setReviewers(r.reviewers);
          setData(r);
          if (!a && r.humans.user_a) setUserA(r.humans.user_a.id);
          if (!b && r.humans.user_b) setUserB(r.humans.user_b.id);
          setError(null);
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setLoading(false));
    },
    [projectId, searchId]
  );

  // Default pair: let the server pick the two most active reviewers.
  // (setState only inside the promise callbacks, as in ComparisonPanel.)
  useEffect(() => {
    let on = true;
    getAgreement(projectId, searchId, {})
      .then((r) => {
        if (!on) return;
        setReviewers(r.reviewers);
        setData(r);
        if (r.humans.user_a) setUserA(r.humans.user_a.id);
        if (r.humans.user_b) setUserB(r.humans.user_b.id);
        setError(null);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (!on) return;
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      });
    return () => {
      on = false;
    };
  }, [projectId, searchId]);

  const pickA = (v: string) => {
    setUserA(v);
    if (v && v !== userB) void fetchAgreement(v, userB, model);
    else setData(null);
  };
  const pickB = (v: string) => {
    setUserB(v);
    if (v && v !== userA) void fetchAgreement(userA, v, model);
    else setData(null);
  };
  const pickModel = (v: string) => {
    setModel(v);
    void fetchAgreement(userA, userB, v);
  };

  if (loading && !data)
    return (
      <Card title={t("title")}>
        <Skeleton rows={3} />
      </Card>
    );
  if (reviewers.length === 0) return null; // nothing annotated: no agreement to show

  const h = data?.humans;
  const ai = data?.ai ?? null;

  return (
    <Card title={t("title")}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-muted">
          A
          <Select value={userA} onChange={(e) => pickA(e.target.value)} aria-label="A">
            {reviewers.map((r) => (
              <option key={r.id} value={r.id}>
                {r.email} ({r.count})
              </option>
            ))}
          </Select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted">
          B
          <Select value={userB} onChange={(e) => pickB(e.target.value)} aria-label="B">
            {reviewers.map((r) => (
              <option key={r.id} value={r.id}>
                {r.email} ({r.count})
              </option>
            ))}
          </Select>
        </label>
        {models.length > 0 && (
          <label className="flex items-center gap-1.5 text-xs text-muted">
            {t("aiModel")}
            <Select value={model} onChange={(e) => pickModel(e.target.value)} aria-label={t("aiModel")}>
              <option value="">{t("noModel")}</option>
              {models.map((m) => (
                <option key={m.id} value={m.name}>
                  {m.name}
                </option>
              ))}
            </Select>
          </label>
        )}
      </div>

      {error && <ErrorBanner message={error} />}

      {!h ? (
        <p className="text-sm text-muted">{t("pickReviewers")}</p>
      ) : h.n === 0 ? (
        <p className="text-sm text-muted">{t("noOverlap")}</p>
      ) : (
        <>
          {/* Human block: n, agreement %, kappa, per-field table */}
          <div className="mb-3 grid grid-cols-3 gap-2">
            <div className="rounded-lg bg-surface-muted p-2 text-center">
              <div className="text-lg font-bold text-foreground">{h.n}</div>
              <div className="text-xs text-muted">{t("comparedArticles")}</div>
            </div>
            <div className="rounded-lg bg-surface-muted p-2 text-center">
              <div className="text-lg font-bold text-foreground">{pct(h.acc)}</div>
              <div className="text-xs text-muted">{t("agreementRate")}</div>
            </div>
            <div className="rounded-lg bg-surface-muted p-2 text-center">
              <div className="text-lg font-bold text-foreground">{kappa(h.kappa)}</div>
              <div className="text-xs text-muted">{t("kappa")}</div>
            </div>
          </div>
          {h.by_field.length > 1 && (
            <div className="overflow-x-auto rounded-lg">
              <table className="w-full text-left text-sm">
                <thead className="bg-surface-muted text-xs text-muted">
                  <tr>
                    <th className="px-3 py-2">{t("field")}</th>
                    <th className="px-3 py-2">{t("colN")}</th>
                    <th className="px-3 py-2">{t("colAgreement")}</th>
                    <th className="px-3 py-2">{t("kappa")}</th>
                  </tr>
                </thead>
                <tbody>
                  {h.by_field.map((f) => (
                    <tr key={f.field} className="border-t border-border">
                      <td className="px-3 py-2">{f.field}</td>
                      <td className="px-3 py-2">{f.n}</td>
                      <td className="px-3 py-2">{pct(f.acc)}</td>
                      <td className="px-3 py-2">{kappa(f.kappa)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {/* AI block (model chosen): agreement between the model and reviewer B */}
          {ai && ai.n > 0 && (
            <div className="mt-3 rounded-lg bg-surface-muted p-3 text-sm">
              <span className="font-semibold text-foreground">{t("aiAgreement")}</span>
              <span className="ml-2 text-muted">{ai.model}</span>
              <span className="ml-2">
                {ai.n} · {pct(ai.acc)} · {t("kappa")} {kappa(ai.kappa)}
              </span>
            </div>
          )}
          {/* Disagreements, clickable to the article sheet */}
          {data && data.disagreements.length > 0 && (
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
                      A: {fmt(d.value_a)} · B: {fmt(d.value_b)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {/* PRISMA-ready summary */}
          {data && (
            <details className="mt-3">
              <summary className="cursor-pointer text-sm font-semibold text-foreground">
                {t("summary")}
              </summary>
              <p className="mt-1 whitespace-pre-wrap rounded-lg bg-surface-muted p-3 text-sm text-foreground">
                {data.summary}
              </p>
            </details>
          )}
        </>
      )}
    </Card>
  );
}
