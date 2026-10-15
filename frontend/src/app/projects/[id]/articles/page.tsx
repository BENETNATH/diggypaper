"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { getProject, prismaUrl, prismaPdfUrl, listArticles, Article, PredictionFilter, SchemaField } from "@/lib/api";
import { AnswerFilters, Answers } from "@/components/AnswerFilters";
import { ArticleLinks, Certainty, biblioLine } from "@/components/Certainty";
import { BulkPdfUpload } from "@/components/BulkPdfUpload";
import { ExportMenu } from "@/components/ExportMenu";
import { RequireAuth } from "@/components/RequireAuth";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Skeleton, ErrorBanner, EmptyState } from "@/components/ui/Feedback";
import { articlesStoreKey, prefillStoreKey, selectionFromFilters, withSearch } from "@/lib/search";
import { useSearchId } from "@/lib/useSearch";

type Filter = PredictionFilter | "ALL";
const FILTERS: Filter[] = ["ALL", "TO_REVIEW", "AUTO_ACCEPTED", "AUTO_REJECTED", "NONE"];
const PER_PAGE = 100;

type Sort = "added" | "date_desc" | "date_asc" | "score_desc";
/** Filtres mémorisés par projet et par recherche : retrouvés au retour d'un article (ou d'un rechargement). */
function loadSaved(projectId: string, searchId?: string): { filter?: Filter; sort?: Sort; page?: number; answers?: Answers } {
  try {
    return JSON.parse(sessionStorage.getItem(articlesStoreKey(projectId, searchId)) ?? "{}");
  } catch {
    return {};
  }
}

function ResultsContent() {
  const t = useTranslations("articles");
  const tc = useTranslations("common");
  const locale = useLocale();
  const projectId = useParams<{ id: string }>().id;
  const searchId = useSearchId(projectId);
  const router = useRouter();

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
  const restoredFor = useRef(""); // clé de stockage dont les filtres sont restaurés (évite d'écraser une autre recherche)

  // Restaure filtres / tri / page mémorisés (propres à la recherche), puis charge ses items.
  useEffect(() => {
    const key = articlesStoreKey(projectId, searchId);
    restoredFor.current = "";
    setReady(false);
    const saved = loadSaved(projectId, searchId);
    setFilter(saved.filter ?? "ALL");
    setSort(saved.sort ?? "added");
    setPage(saved.page ?? 1);
    setAnswers(saved.answers ?? {});
    setDraft(saved.answers ?? {});
    getProject(projectId, searchId)
      .then((p) => setFields(p.fields ?? []))
      .catch(() => setFields([]))
      .finally(() => {
        restoredFor.current = key;
        setReady(true);
      });
  }, [projectId, searchId]);

  useEffect(() => {
    if (!ready || restoredFor.current !== articlesStoreKey(projectId, searchId)) return;
    try {
      sessionStorage.setItem(
        articlesStoreKey(projectId, searchId),
        JSON.stringify({ filter, sort, page, answers })
      );
    } catch {
      /* stockage indisponible : filtres non mémorisés */
    }
  }, [ready, projectId, searchId, filter, sort, page, answers]);

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
      const res = await listArticles(projectId, page, PER_PAGE, filter === "ALL" ? undefined : filter, sort, answers, searchId);
      setArticles(res.items);
      setTotal(res.total);
      setStats(res.stats);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId, page, filter, sort, answers, searchId]);

  useEffect(() => {
    if (ready && restoredFor.current === articlesStoreKey(projectId, searchId)) void load();
  }, [ready, load, projectId, searchId]);

  const count = (f: Filter) =>
    f === "ALL" ? Object.values(stats).reduce((a, b) => a + b, 0) : stats[f] ?? 0;
  const triaged = count("ALL") - count("NONE") > 0;
  const queue = "list"; // la file de relecture reprend la liste affichée (filtres, tri)

  /** Ouvre la page Recherches avec une sélection préremplie par les filtres courants. */
  function newSearchFromFilters() {
    try {
      sessionStorage.setItem(
        prefillStoreKey(projectId),
        JSON.stringify(selectionFromFilters(filter, answers, searchId))
      );
    } catch {
      /* stockage indisponible : la page Recherches s'ouvre sans préremplissage */
    }
    router.push(withSearch(`/projects/${projectId}/searches`, searchId));
  }

  if (error) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!articles) return <Skeleton rows={5} />;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <ExportMenu projectId={projectId} status={filter === "ALL" ? undefined : filter} answers={answers} />
        {count("TO_REVIEW") > 0 && (
          <Link href={withSearch(`/projects/${projectId}/annotate?queue=review`, searchId)}>
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
          <img src={prismaUrl(projectId, locale, searchId)} alt={t("prisma")} className="w-full max-w-3xl rounded border border-border" />
          <a href={prismaUrl(projectId, locale, searchId)} download="prisma.svg" className="text-xs text-primary hover:underline">
            ⬇ {t("prismaDownload")}
          </a>
          <a href={prismaPdfUrl(projectId, locale, searchId)} download className="text-xs text-primary hover:underline">
            ⬇ {t("prismaDownloadPdf")}
          </a>
        </div>
      </details>

      {!triaged && count("ALL") > 0 && (
        <div className="rounded-lg border border-primary/40 bg-primary/5 p-3 text-sm">
          {t("notTriaged")}{" "}
          <Link href={withSearch(`/projects/${projectId}/inference`, searchId)} className="font-medium text-primary hover:underline">
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

      {total > 0 && (
        <div className="flex justify-end">
          <Button variant="secondary" size="sm" onClick={newSearchFromFilters}>
            {t("newSearchOnThese", { n: total })}
          </Button>
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
                  href={withSearch(`/projects/${projectId}/annotate?article=${a.id}&queue=${queue}`, searchId)}
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
