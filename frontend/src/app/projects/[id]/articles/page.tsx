"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { getProject, prismaUrl, prismaPdfUrl, listArticles, Article, PredictionFilter, SchemaField } from "@/lib/api";
import { AnswerFilters, Answers } from "@/components/AnswerFilters";
import { BulkPdfUpload } from "@/components/BulkPdfUpload";
import { ExportMenu } from "@/components/ExportMenu";
import { ResultRow } from "@/components/ResultRow";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { Modal } from "@/components/ui/Modal";
import { RequireAuth } from "@/components/RequireAuth";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Skeleton, ErrorBanner, EmptyState } from "@/components/ui/Feedback";
import { articlesStoreKey, prefillStoreKey, selectionFromFilters, withSearch } from "@/lib/search";
import { useSearchId } from "@/lib/useSearch";

type Filter = PredictionFilter | "ALL";
const FILTERS: Filter[] = ["ALL", "TO_REVIEW", "AUTO_ACCEPTED", "AUTO_REJECTED", "NONE"];
const PER_PAGE = 100;

type Sort = "added" | "date_desc" | "date_asc" | "score_desc";
/** Filtres mémorisés par projet et par recherche : retrouvés au retour d'un article (ou d'un rechargement). */
function loadSaved(projectId: string, searchId?: string): { filter?: Filter; sort?: Sort; page?: number; answers?: Answers; q?: string; fulltext?: boolean } {
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
  const [pdfOpen, setPdfOpen] = useState(false);
  const [prismaOpen, setPrismaOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [answers, setAnswers] = useState<Answers>({}); // appliqués à la requête
  const [draft, setDraft] = useState<Answers>({}); // saisie (texte retardé de 300 ms)
  const [text, setText] = useState(""); // recherche rapide appliquée (titre, auteurs, PMID, DOI)
  const [textDraft, setTextDraft] = useState("");
  const [fulltext, setFulltext] = useState<boolean | undefined>(undefined); // texte intégral (true) / résumé seul (false)
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
    setText(saved.q ?? "");
    setTextDraft(saved.q ?? "");
    setFulltext(saved.fulltext);
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
        JSON.stringify({ filter, sort, page, answers, q: text, fulltext })
      );
    } catch {
      /* stockage indisponible : filtres non mémorisés */
    }
  }, [ready, projectId, searchId, filter, sort, page, answers, text, fulltext]);

  // Recherche rapide : appliquée 300 ms après la dernière frappe, repart à la première page.
  useEffect(() => {
    if (textDraft === text) return;
    const id = setTimeout(() => {
      setText(textDraft);
      setPage(1);
    }, 300);
    return () => clearTimeout(id);
  }, [textDraft, text]);

  function setAnswer(key: string, value: string, isText: boolean) {
    setDraft((d) => ({ ...d, [key]: value }));
    clearTimeout(timer.current);
    const apply = () => {
      setAnswers((a) => ({ ...a, [key]: value }));
      setPage(1);
    };
    if (isText) timer.current = setTimeout(apply, 300);
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
      const res = await listArticles(projectId, page, PER_PAGE, filter === "ALL" ? undefined : filter, sort, answers, searchId, text, fulltext);
      setArticles(res.items);
      setTotal(res.total);
      setStats(res.stats);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId, page, filter, sort, answers, searchId, text, fulltext]);

  useEffect(() => {
    if (ready && restoredFor.current === articlesStoreKey(projectId, searchId)) void load();
  }, [ready, load, projectId, searchId]);

  const count = (f: Filter) =>
    f === "ALL"
      ? Object.entries(stats).reduce((a, [k, n]) => (k === "TO_REVIEW" ? a : a + n), 0) // TO_REVIEW recoupe NEEDS_HUMAN_REVIEW
      : stats[f] ?? 0;
  const triaged = count("ALL") - count("NONE") > 0;
  const queue = "list"; // la file de relecture reprend la liste affichée (filtres, tri)

  /** Ouvre la page Recherches avec une sélection préremplie par les filtres courants. */
  function newSearchFromFilters() {
    try {
      sessionStorage.setItem(
        prefillStoreKey(projectId),
        JSON.stringify(selectionFromFilters(filter, answers, searchId, text, fulltext))
      );
    } catch {
      /* stockage indisponible : la page Recherches s'ouvre sans préremplissage */
    }
    router.push(withSearch(`/projects/${projectId}/searches`, searchId));
  }

  if (error) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!articles) return <Skeleton rows={5} />;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold text-foreground">
          {t("title")} <span className="text-sm font-normal text-muted">{total.toLocaleString()}</span>
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {count("TO_REVIEW") > 0 && (
            <Link href={withSearch(`/projects/${projectId}/annotate?queue=review`, searchId)}>
              <Button size="sm">
                {t("startReview")} ({count("TO_REVIEW")})
              </Button>
            </Link>
          )}
          <ExportMenu projectId={projectId} status={filter === "ALL" ? undefined : filter} answers={answers} text={text} fulltext={fulltext} />
          <Menu label={t("tools")}>
            <MenuItem onClick={() => setPdfOpen(true)}>{t("bulkTitle")}</MenuItem>
            <MenuItem onClick={() => setPrismaOpen(true)}>{t("prisma")}</MenuItem>
            {total > 0 && <MenuItem onClick={newSearchFromFilters}>{t("newSearchOnThese", { n: total })}</MenuItem>}
          </Menu>
        </div>
      </div>

      {!triaged && count("ALL") > 0 && (
        <p className="rounded-lg border border-primary/40 bg-primary/5 px-3 py-2 text-sm">
          {t("notTriaged")}{" "}
          <Link href={withSearch(`/projects/${projectId}/inference`, searchId)} className="font-medium text-primary hover:underline">
            {t("goTriage")} →
          </Link>
        </p>
      )}

      <div className="relative">
        <span aria-hidden className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted">
          ⌕
        </span>
        <input
          type="search"
          value={textDraft}
          onChange={(e) => setTextDraft(e.target.value)}
          placeholder={t("searchPlaceholder")}
          aria-label={t("searchLabel")}
          className="w-full rounded-lg border border-border bg-surface py-2 pl-8 pr-9 text-sm text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
        />
        {textDraft && (
          <button
            type="button"
            onClick={() => {
              setTextDraft("");
              setText("");
              setPage(1);
            }}
            aria-label={t("searchClear")}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1.5 text-lg leading-none text-muted hover:text-foreground"
          >
            ×
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
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
        <div className="ml-auto flex items-center gap-2">
          {fields.length > 0 && (
            <button
              type="button"
              aria-expanded={filtersOpen || hasAnswerFilter}
              onClick={() => setFiltersOpen((o) => !o)}
              className={`rounded-lg border px-3 py-1 text-sm ${
                hasAnswerFilter ? "border-primary bg-primary/10 text-primary" : "border-border text-foreground hover:bg-surface-muted"
              }`}
            >
              {t("filterByAnswer")}
              {hasAnswerFilter && <span className="ml-1 text-xs">•</span>}
            </button>
          )}
          <select
            value={fulltext === undefined ? "" : fulltext ? "yes" : "no"}
            aria-label={t("textFilter")}
            onChange={(e) => {
              setFulltext(e.target.value === "" ? undefined : e.target.value === "yes");
              setPage(1);
            }}
            className={`rounded-lg border bg-surface px-2 py-1 text-sm text-foreground ${
              fulltext === undefined ? "border-border" : "border-primary bg-primary/10"
            }`}
          >
            <option value="">{t("textAll")}</option>
            <option value="yes">{t("textFull")}</option>
            <option value="no">{t("textAbstract")}</option>
          </select>
          <select
            value={sort}
            aria-label={t("sortBy")}
            onChange={(e) => {
              setSort(e.target.value as Sort);
              setPage(1);
            }}
            className="rounded-lg border border-border bg-surface px-2 py-1 text-sm text-foreground"
          >
            <option value="added">{t("sortAdded")}</option>
            <option value="date_desc">{t("sortDateDesc")}</option>
            <option value="date_asc">{t("sortDateAsc")}</option>
            <option value="score_desc">{t("sortScore")}</option>
          </select>
        </div>
      </div>

      {fields.length > 0 && (filtersOpen || hasAnswerFilter) && (
        <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-surface p-3">
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

      {pdfOpen && (
        <Modal title={t("bulkTitle")} onClose={() => setPdfOpen(false)}>
          <BulkPdfUpload projectId={projectId} onDone={() => void load()} />
        </Modal>
      )}
      {prismaOpen && (
        <Modal title={t("prisma")} onClose={() => setPrismaOpen(false)} wide>
          <div className="flex flex-col items-start gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={prismaUrl(projectId, locale, searchId)} alt={t("prisma")} className="w-full rounded border border-border" />
            <div className="flex gap-4 text-sm">
              <a href={prismaUrl(projectId, locale, searchId)} download="prisma.svg" className="text-primary hover:underline">
                ⬇ {t("prismaDownload")}
              </a>
              <a href={prismaPdfUrl(projectId, locale, searchId)} download className="text-primary hover:underline">
                ⬇ {t("prismaDownloadPdf")}
              </a>
            </div>
          </div>
        </Modal>
      )}

      {articles.length === 0 ? (
        <EmptyState
          title={t("empty")}
          hint={filter !== "ALL" || text || hasAnswerFilter || fulltext !== undefined ? t("emptyFiltered") : t("emptyHint")}
        />
      ) : (
        <Card className="p-1 sm:p-2">
          <ul className="divide-y divide-border/60">
            {articles.map((a) => (
              <ResultRow
                key={a.id}
                a={a}
                href={withSearch(`/projects/${projectId}/annotate?article=${a.id}&queue=${queue}`, searchId)}
              />
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
