"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { getProject, prismaUrl, prismaPdfUrl, listArticles, Article, PredictionFilter, SchemaField } from "@/lib/api";
import { ArticleLinks, Certainty, biblioLine } from "@/components/Certainty";
import { BulkPdfUpload } from "@/components/BulkPdfUpload";
import { ExportMenu } from "@/components/ExportMenu";
import { RequireAuth } from "@/components/RequireAuth";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Skeleton, ErrorBanner, EmptyState } from "@/components/ui/Feedback";

type Filter = PredictionFilter | "ALL";
const FILTERS: Filter[] = ["ALL", "TO_REVIEW", "AUTO_ACCEPTED", "AUTO_REJECTED", "NONE"];
const PER_PAGE = 100;

type Sort = "added" | "date_desc" | "date_asc" | "score_desc";
type Answers = Record<string, string>;

/** Filtres mémorisés par projet : retrouvés au retour d'un article (ou d'un rechargement). */
const storeKey = (projectId: string) => `diggy:articles:${projectId}`;

function loadSaved(projectId: string): { filter?: Filter; sort?: Sort; page?: number; answers?: Answers } {
  try {
    return JSON.parse(sessionStorage.getItem(storeKey(projectId)) ?? "{}");
  } catch {
    return {};
  }
}

const selectCls = "rounded border border-border bg-surface p-1 text-xs text-foreground";

function AnswerFilters({
  fields,
  values,
  onChange,
}: {
  fields: SchemaField[];
  values: Answers;
  onChange: (key: string, value: string) => void;
}) {
  const t = useTranslations("articles");
  return (
    <>
      {fields.map((f) => (
        <label key={f.key} className="flex flex-col gap-0.5 text-xs text-muted">
          {f.label}
          {f.type === "text" ? (
            <input
              type="search"
              value={values[f.key] ?? ""}
              placeholder={t("containsText")}
              onChange={(e) => onChange(f.key, e.target.value)}
              className={`${selectCls} w-40`}
            />
          ) : (
            <select value={values[f.key] ?? ""} onChange={(e) => onChange(f.key, e.target.value)} className={selectCls}>
              <option value="">{t("anyAnswer")}</option>
              {f.type === "boolean" ? (
                <>
                  <option value="true">{t("yes")}</option>
                  <option value="false">{t("no")}</option>
                </>
              ) : (
                f.options?.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))
              )}
            </select>
          )}
        </label>
      ))}
    </>
  );
}

function ResultsContent() {
  const t = useTranslations("articles");
  const tc = useTranslations("common");
  const locale = useLocale();
  const projectId = useParams<{ id: string }>().id;

  const [articles, setArticles] = useState<Article[] | null>(null);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [sort, setSort] = useState<Sort>("added");
  const [fields, setFields] = useState<SchemaField[]>([]);
  const [answers, setAnswers] = useState<Answers>({}); // appliqués à la requête
  const [draft, setDraft] = useState<Answers>({}); // saisie (texte retardé de 300 ms)
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  // Restaure filtres / tri / page mémorisés, puis charge la définition des questions.
  useEffect(() => {
    const saved = loadSaved(projectId);
    if (saved.filter) setFilter(saved.filter);
    if (saved.sort) setSort(saved.sort);
    if (saved.page) setPage(saved.page);
    setAnswers(saved.answers ?? {});
    setDraft(saved.answers ?? {});
    getProject(projectId)
      .then((p) => setFields(p.fields ?? []))
      .catch(() => setFields([]))
      .finally(() => setReady(true));
  }, [projectId]);

  useEffect(() => {
    if (!ready) return;
    try {
      sessionStorage.setItem(storeKey(projectId), JSON.stringify({ filter, sort, page, answers }));
    } catch {
      /* stockage indisponible : filtres non mémorisés */
    }
  }, [ready, projectId, filter, sort, page, answers]);

  function setAnswer(key: string, value: string, text: boolean) {
    setDraft((d) => ({ ...d, [key]: value }));
    clearTimeout(timer.current);
    const apply = () => {
      setAnswers((a) => ({ ...a, [key]: value }));
      setPage(1);
    };
    if (text) timer.current = setTimeout(apply, 300);
    else apply();
  }

  const hasAnswerFilter = Object.values(answers).some((v) => v !== "");
  function resetAnswers() {
    clearTimeout(timer.current);
    setAnswers({});
    setDraft({});
    setPage(1);
  }

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await listArticles(projectId, page, PER_PAGE, filter === "ALL" ? undefined : filter, sort, answers);
      setArticles(res.items);
      setTotal(res.total);
      setStats(res.stats);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId, page, filter, sort, answers]);

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  const count = (f: Filter) =>
    f === "ALL" ? Object.values(stats).reduce((a, b) => a + b, 0) : stats[f] ?? 0;
  const triaged = count("ALL") - count("NONE") > 0;
  const queue = "list"; // la file de relecture reprend la liste affichée (filtres, tri)

  if (error) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!articles) return <Skeleton rows={5} />;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <ExportMenu projectId={projectId} status={filter === "ALL" ? undefined : filter} answers={answers} />
        {count("TO_REVIEW") > 0 && (
          <Link href={`/projects/${projectId}/annotate?queue=review`}>
            <Button>
              {t("startReview")} ({count("TO_REVIEW")})
            </Button>
          </Link>
        )}
      </div>

      <details className="rounded-lg border border-border bg-surface p-3">
        <summary className="cursor-pointer text-sm font-medium text-foreground">{t("bulkTitle")}</summary>
        <div className="mt-3">
          <BulkPdfUpload projectId={projectId} onDone={() => void load()} />
        </div>
      </details>

      <details className="rounded-lg border border-border bg-surface p-3">
        <summary className="cursor-pointer text-sm font-medium text-foreground">{t("prisma")}</summary>
        <div className="mt-3 flex flex-col items-start gap-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={prismaUrl(projectId, locale)} alt={t("prisma")} className="w-full max-w-3xl rounded border border-border" />
          <a href={prismaUrl(projectId, locale)} download="prisma.svg" className="text-xs text-primary hover:underline">
            ⬇ {t("prismaDownload")}
          </a>
          <a href={prismaPdfUrl(projectId, locale)} download className="text-xs text-primary hover:underline">
            ⬇ {t("prismaDownloadPdf")}
          </a>
        </div>
      </details>

      {!triaged && count("ALL") > 0 && (
        <div className="rounded-lg border border-primary/40 bg-primary/5 p-3 text-sm">
          {t("notTriaged")}{" "}
          <Link href={`/projects/${projectId}/inference`} className="font-medium text-primary hover:underline">
            {t("goTriage")} →
          </Link>
        </div>
      )}

      <div className="flex items-center justify-end gap-2 text-xs text-muted">
        {t("sortBy")}
        <select
          value={sort}
          onChange={(e) => {
            setSort(e.target.value as Sort);
            setPage(1);
          }}
          className="rounded border border-border bg-surface p-1 text-xs text-foreground"
        >
          <option value="added">{t("sortAdded")}</option>
          <option value="date_desc">{t("sortDateDesc")}</option>
          <option value="date_asc">{t("sortDateAsc")}</option>
          <option value="score_desc">{t("sortScore")}</option>
        </select>
      </div>

      <div className="flex flex-wrap gap-2" role="tablist">
        {FILTERS.filter((f) => f === "ALL" || count(f) > 0 || f === filter).map((f) => (
          <button
            key={f}
            role="tab"
            aria-selected={filter === f}
            onClick={() => {
              setFilter(f);
              setPage(1);
            }}
            className={`rounded-full border px-3 py-1 text-sm ${
              filter === f
                ? "border-primary bg-primary/10 font-medium text-primary"
                : "border-border text-muted hover:bg-surface-muted"
            }`}
          >
            {t(`filter_${f}`)} <span className="text-xs">({count(f)})</span>
          </button>
        ))}
      </div>

      {fields.length > 0 && (
        <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-surface p-3">
          <span className="text-xs font-medium text-foreground">{t("filterByAnswer")}</span>
          <AnswerFilters
            fields={fields}
            values={draft}
            onChange={(k, v) => setAnswer(k, v, fields.find((f) => f.key === k)?.type === "text")}
          />
          {hasAnswerFilter && (
            <Button variant="secondary" size="sm" onClick={resetAnswers}>
              {t("resetFilters")}
            </Button>
          )}
          <span className="ml-auto text-xs text-muted">
            {total} {t("resultsCount")}
          </span>
        </div>
      )}

      {articles.length === 0 ? (
        <EmptyState title={t("empty")} hint={t("emptyHint")} />
      ) : (
        <Card>
          <ul className="divide-y divide-border">
            {articles.map((a) => (
              <li key={a.id} className="py-1">
                <Link
                  href={`/projects/${projectId}/annotate?article=${a.id}&queue=${queue}`}
                  className="flex items-center justify-between gap-3 py-2 hover:bg-surface-muted"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">{a.title ?? t("untitled")}</p>
                    {biblioLine(a) && <p className="truncate text-xs text-foreground/80">{biblioLine(a)}</p>}
                    <p className="text-xs text-muted">
                      {a.pmid ? `PMID ${a.pmid}` : a.id}
                      {a.has_fulltext
                        ? ` · ${t("fulltext")}${a.fulltext_source && a.fulltext_source !== "pmc" ? ` (${a.fulltext_source})` : ""}`
                        : ` · ${t("abstractOnly")}`}
                    </p>
                    {(a.answers?.length ?? 0) > 0 && (
                      <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
                        {a.answers!.map((r) => (
                          <span key={r.key} className="inline-flex items-center gap-1">
                            {r.label} :{" "}
                            <strong className="text-foreground">
                              {typeof r.value === "boolean" ? (r.value ? t("yes") : t("no")) : String(r.value)}
                            </strong>
                            {r.source === "human" ? (
                              <span title={t("humanAnswer")} className="text-primary">
                                ✎
                              </span>
                            ) : (
                              <Certainty value={r.confidence} />
                            )}
                          </span>
                        ))}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {a.confidence != null && (
                      <span className="text-xs text-muted" title={t("relevanceHelp")}>
                        {t("relevance")} {Math.round(a.confidence * 100)} %
                      </span>
                    )}
                    {a.annotated && (
                      <Badge color={a.review?.status === "CORRECTED" ? "yellow" : "green"}>
                        ✓ {a.review?.status ? t(`review_${a.review.status}`) : t("reviewed")}
                        {a.review?.by ? ` · ${t("reviewedBy", { name: a.review.by })}` : ""}
                      </Badge>
                    )}
                    {a.prediction_status && (
                      <Badge color={statusColor(a.prediction_status)}>{t(`status_${a.prediction_status}`)}</Badge>
                    )}
                  </div>
                </Link>
                <ArticleLinks article={a} className="pb-1" />
              </li>
            ))}
          </ul>
        </Card>
      )}

      {total > PER_PAGE && (
        <div className="flex items-center justify-between gap-2">
          <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            {tc("previous")}
          </Button>
          <span className="text-sm text-muted">
            {page} / {Math.ceil(total / PER_PAGE)}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={page >= Math.ceil(total / PER_PAGE)}
            onClick={() => setPage((p) => p + 1)}
          >
            {tc("next")}
          </Button>
        </div>
      )}
    </div>
  );
}

export default function ArticlesPage() {
  return (
    <RequireAuth>
      <ResultsContent />
    </RequireAuth>
  );
}
