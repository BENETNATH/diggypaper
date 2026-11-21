"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { listArticles, Article } from "@/lib/api";
import { useApiError } from "@/lib/errorMsg";
import { Button } from "@/components/ui/Button";
import { CopyButton } from "@/components/CopyButton";
import { PdfDropzone } from "@/components/PdfDropzone";
import { EmptyState, ErrorBanner, Skeleton } from "@/components/ui/Feedback";

const PER_PAGE = 50;
type CorpusSort = "added" | "date_desc" | "date_asc";

/** Provenance chip: the origin value → its i18n label (unknown origin: no chip). */
const ORIGIN_KEY: Record<string, string> = {
  pubmed: "corpusOrigin_pubmed",
  references: "corpusOrigin_references",
  citation: "corpusOrigin_citation",
  upload: "corpusOrigin_file",
};

/**
 * The corpus without any analysis (spec 2026-11-20-corpus-view R2): the whole
 * project's articles, structured rows, quick search + date/import sort, 50 per page.
 * A row opens the existing article sheet (no search param).
 */
export function CorpusList({
  projectId,
  isLead,
  articleCount,
  fulltextCount = null,
}: {
  projectId: string;
  isLead: boolean;
  /** Corpus size from the overview (stable: independent of the quick search). */
  articleCount: number;
  /** Articles with a full text (overview); null/0 = not shown in the counter. */
  fulltextCount?: number | null;
}) {
  const t = useTranslations("articles");
  const tc = useTranslations("common");
  const locale = useLocale();
  const errText = useApiError();
  const [articles, setArticles] = useState<Article[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<CorpusSort>("added");
  const [text, setText] = useState(""); // quick search applied (title, authors, PMID, DOI)
  const [textDraft, setTextDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Quick search applied 300 ms after the last keystroke (same as the scoped list).
  useEffect(() => {
    if (textDraft === text) return;
    const id = setTimeout(() => {
      setText(textDraft);
      setPage(1);
    }, 300);
    return () => clearTimeout(id);
  }, [textDraft, text]);

  const load = useCallback(() => {
    return listArticles(projectId, page, PER_PAGE, undefined, sort, {}, undefined, text || undefined)
      .then((res) => {
        setArticles(res.items);
        setTotal(res.total);
        setError(null);
      })
      .catch((e) => setError(errText(e)));
  }, [projectId, page, sort, text, errText]);

  useEffect(() => {
    void load();
  }, [load]);

  const counter = fulltextCount
    ? t("corpusCountFulltext", { n: articleCount, k: fulltextCount })
    : t("corpusCount", { n: articleCount });

  const listCard = (
    <div className="overflow-hidden rounded-xl bg-surface shadow-sm">
      <ul className="divide-y divide-border/60">
        {(articles ?? []).map((a) => {
          const sub = [
            a.first_author ? `${a.first_author} et al.` : null,
            a.journal ?? null,
            a.pub_date ? new Date(a.pub_date).toLocaleDateString(locale) : null,
          ]
            .filter(Boolean)
            .join(" · ");
          return (
            <li key={a.id}>
              <Link
                href={`/projects/${projectId}/annotate?article=${a.id}`}
                className="group flex items-center gap-3 px-3.5 py-3 hover:bg-surface-muted"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <p
                      title={a.title ?? ""}
                      className="min-w-0 truncate text-sm font-semibold text-foreground group-hover:text-primary"
                    >
                      {a.title || t("untitled")}
                    </p>
                    {a.title && (
                      <CopyButton
                        value={a.title}
                        label={tc("copyTitle")}
                        title={tc("titleNoun")}
                        size="sm"
                        className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                      />
                    )}
                  </div>
                  {sub && (
                    <p className="mt-0.5 text-xs text-muted">
                      {sub}
                      {a.doi && a.doi_url && (
                        <>
                          {" · "}
                          <a
                            href={a.doi_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:underline"
                          >
                            DOI
                          </a>
                          <CopyButton
                            value={a.doi}
                            label={tc("copyDoi")}
                            title="DOI"
                            size="sm"
                            className="ml-0.5 align-[-2px] opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                          />
                        </>
                      )}
                    </p>
                  )}
                </div>
                {a.origin && ORIGIN_KEY[a.origin] && (
                  <span className="whitespace-nowrap rounded-full border border-border px-2 py-0.5 text-[11px] text-muted">
                    {t(ORIGIN_KEY[a.origin])}
                  </span>
                )}
                <span
                  className={`whitespace-nowrap text-xs ${
                    a.has_fulltext ? "font-semibold text-success" : "text-muted"
                  }`}
                >
                  {a.has_fulltext ? "✓" : "—"}{" "}
                  {a.has_fulltext ? t("corpusFulltext") : t("corpusAbstractOnly")}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-2.5">
        <h1 className="text-xl font-bold text-foreground">{t("corpusTitle")}</h1>
        <span className="text-sm text-muted">{counter}</span>
      </div>

      {isLead && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-primary/35 bg-primary/5 px-3.5 py-2.5 text-sm">
          <span>{t("corpusCta")}</span>
          <Link
            href={`/projects/${projectId}/inference`}
            className="font-semibold text-primary hover:underline"
          >
            {t("corpusCtaAction")} →
          </Link>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <input
          type="search"
          value={textDraft}
          onChange={(e) => setTextDraft(e.target.value)}
          placeholder={t("corpusSearchPlaceholder")}
          aria-label={t("searchLabel")}
          className="min-w-40 flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
        />
        <select
          value={sort}
          aria-label={t("sortBy")}
          onChange={(e) => {
            setSort(e.target.value as CorpusSort);
            setPage(1);
          }}
          className="rounded-lg border border-border bg-surface px-2 py-2 text-sm text-foreground"
        >
          <option value="added">{t("corpusSortAdded")}</option>
          <option value="date_desc">{t("corpusSortDateDesc")}</option>
          <option value="date_asc">{t("corpusSortDateAsc")}</option>
        </select>
      </div>

      {error ? (
        <ErrorBanner message={error} onRetry={() => void load()} />
      ) : articles === null ? (
        <Skeleton rows={5} />
      ) : articles.length === 0 ? (
        <EmptyState title={t("empty")} hint={text ? t("emptyFiltered") : t("emptyHint")} />
      ) : (
        <>
          {/* spec 2026-11-21 R6: the whole card is the PDF drop zone for a lead. */}
          {isLead ? (
            <PdfDropzone projectId={projectId} variant="overlay" onDone={() => void load()}>
              {listCard}
            </PdfDropzone>
          ) : (
            listCard
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm text-muted">
              {t("corpusPages", { from: (page - 1) * PER_PAGE + 1, to: Math.min(page * PER_PAGE, total), n: total })}
            </span>
            <div className="flex gap-1.5">
              <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                {tc("previous")}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= Math.ceil(total / PER_PAGE)}
                onClick={() => setPage((p) => p + 1)}
              >
                {tc("next")}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
