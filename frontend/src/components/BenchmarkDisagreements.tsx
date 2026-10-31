"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { listBenchmarkArticles, BenchmarkArticleRow, SchemaField } from "@/lib/api";
import { relevanceOf, showValue } from "@/lib/benchmark";
import { withSearch } from "@/lib/search";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Card";
import { Checkbox } from "@/components/ui/Field";
import { ErrorBanner, Skeleton } from "@/components/ui/Feedback";

const PER_PAGE = 25;

const same = (a: unknown, b: unknown): boolean =>
  String(a ?? "").trim().toLowerCase() === String(b ?? "").trim().toLowerCase();

/** Answers of a decision: one item per row, in red when they deviate from the reference. */
function Answers({
  decision,
  gold,
  fields,
}: {
  decision: Record<string, unknown> | null;
  gold?: Record<string, unknown> | null;
  fields: SchemaField[];
}) {
  const t = useTranslations("benchmarks");
  return (
    <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-xs">
      {fields.map((f) => {
        const wrong = gold && !same(decision?.[f.key], gold[f.key]);
        return (
          <div key={f.key} className="contents">
            <dt className="truncate text-muted" title={f.label}>
              {f.label}
            </dt>
            <dd className={`text-right font-medium ${wrong ? "text-danger" : "text-foreground"}`}>
              {showValue(decision?.[f.key])}
            </dd>
          </div>
        );
      })}
      <dt className="text-muted">{t("relevanceLabel")}</dt>
      <dd className="text-right font-medium text-foreground">{relevanceOf(decision)}</dd>
    </dl>
  );
}

/**
 * Articles of a benchmark, disagreements first: each (model, variant)'s answer side by side
 * with the gold reference when it exists.
 */
export function BenchmarkDisagreements({
  projectId,
  runId,
  searchId,
  fields,
}: {
  projectId: string;
  runId: string;
  searchId?: string | null;
  fields: SchemaField[];
}) {
  const t = useTranslations("benchmarks");
  const [onlyDisagree, setOnlyDisagree] = useState(true);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ total: number; items: BenchmarkArticleRow[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stale = false;
    setData(null);
    setError(null);
    listBenchmarkArticles(projectId, runId, { disagree: onlyDisagree, page, perPage: PER_PAGE })
      .then((r) => !stale && setData(r))
      .catch((e) => !stale && setError(e instanceof Error ? e.message : "error"));
    return () => {
      stale = true;
    };
  }, [projectId, runId, onlyDisagree, page]);

  const pages = data ? Math.max(1, Math.ceil(data.total / PER_PAGE)) : 1;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h4 className="text-sm font-semibold text-foreground">{t("disagreeTitle")}</h4>
        <Checkbox
          label={t("onlyDisagree")}
          checked={onlyDisagree}
          onChange={(e) => {
            setOnlyDisagree(e.target.checked);
            setPage(1);
          }}
        />
      </div>
      <p className="text-xs text-muted">{t("disagreeHint")}</p>
      {error && <ErrorBanner message={error} />}
      {!data && !error && <Skeleton rows={3} height="h-10" />}
      {data && data.items.length === 0 && <p className="text-sm text-muted">{onlyDisagree ? t("noDisagree") : t("noArticles")}</p>}
      {data?.items.map((row) => (
        <article key={row.article_id} className="rounded-lg border border-border p-3">
          <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
            <Link
              href={withSearch(`/projects/${projectId}/annotate?article=${row.article_id}`, searchId ?? undefined)}
              className="min-w-0 text-sm font-medium text-primary hover:underline"
            >
              {row.title ?? row.article_id}
            </Link>
            {row.disagree && <Badge color="yellow">{t("disagreeBadge")}</Badge>}
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {row.gold && (
              <div className="rounded-lg border border-primary/40 bg-primary/5 p-2">
                <p className="mb-1 text-xs font-semibold text-primary">{t("goldRef")}</p>
                <Answers decision={row.gold} fields={fields} />
              </div>
            )}
            {row.outputs.map((o) => (
              <div key={`${o.model}\u0000${o.variant ?? ""}`} className="rounded-lg bg-surface-muted p-2">
                <p className="mb-1 truncate text-xs font-semibold text-foreground" title={`${o.model} · ${o.variant ?? ""}`}>
                  {o.model}
                  {o.variant ? ` · ${o.variant}` : ""}
                </p>
                {o.error ? (
                  <p className="text-xs text-danger">{o.error}</p>
                ) : (
                  <Answers decision={o.decision} gold={row.gold} fields={fields} />
                )}
              </div>
            ))}
          </div>
        </article>
      ))}
      {data && data.total > PER_PAGE && (
        <div className="flex items-center justify-between gap-2">
          <Button type="button" variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            ← {t("prev")}
          </Button>
          <span className="text-xs text-muted">{t("pageOf", { page, pages, total: data.total })}</span>
          <Button type="button" variant="secondary" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>
            {t("next")} →
          </Button>
        </div>
      )}
    </div>
  );
}
