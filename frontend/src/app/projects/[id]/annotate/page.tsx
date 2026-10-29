"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArticleLinks, biblioLine } from "@/components/Certainty";
import { sourceLabel } from "@/lib/source";
import { AnnotationForm, Annotation } from "@/components/AnnotationForm";
import { ModelRef } from "@/components/ModelRef";
import { EvidenceHighlighter } from "@/components/EvidenceHighlighter";
import { SwipeableCard } from "@/components/SwipeableCard";
import { Button } from "@/components/ui/Button";
import { addQuote } from "@/lib/highlight";
import { articlesStoreKey, withSearch } from "@/lib/search";
import { useSearchId, useSearches } from "@/lib/useSearch";
import {
  Article,
  ArticlePrediction,
  ArticleReview,
  getArticleWithPrediction,
  getPrediction,
  getProject,
  goldFromReviews,
  listArticles,
  PredictionFilter,
  SchemaField,
  submitAnnotation,
  uploadArticlePdf,
  searchArticleFulltext,
} from "@/lib/api";

/**
 * Viewer d'annotation mobile-first (MODULE 5).
 *
 * - Desktop : double colonne (document à gauche, formulaire à droite).
 * - Mobile / tablette : vue mono-colonne à onglets (Document / Annotation)
 *   avec barre de navigation inférieure.
 * - Surlignage dynamique des `evidence_quotes` (EvidenceHighlighter).
 * - Swipe : droite = confirmer la réponse du modèle, gauche = corriger (formulaire).
 */
export default function AnnotatePage() {
  const t = useTranslations("annotate");
  const ti = useTranslations("import"); // libellés des parties du texte
  const tc = useTranslations("common");
  const ta = useTranslations("articles"); // libellés de la source
  const locale = useLocale();
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const projectId = params.id;
  const articleId = searchParams.get("article") ?? "";
  // queue=review : les cas à relire (défaut après un tri), restreints aux filtres de réponses de
  // la liste ; list : exactement la liste filtrée/triée qu'on vient de quitter ; all : tous.
  const queueMode = searchParams.get("queue") ?? "review";
  const searchId = useSearchId(projectId);
  const { searches, current } = useSearches(projectId, searchId);
  // Recherche à l'aveugle : on relit sans voir la réponse du modèle (gold non biaisé).
  const isBlind = current?.kind === "blind";

  const [article, setArticle] = useState<Article | null>(null);
  const [prediction, setPrediction] = useState<ArticlePrediction | null>(null);
  const [review, setReview] = useState<ArticleReview | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"document" | "annotation">("document");
  const router = useRouter();
  const [fields, setFields] = useState<SchemaField[]>([]);
  const [sections, setSections] = useState<string[]>(["methods"]); // parties du texte lues par la recherche
  const [queue, setQueue] = useState<string[]>([]);
  const [status, setStatus] = useState<
    "idle" | "accepted" | "rejected" | "submitted"
  >("idle");
  // Extraits de preuve de l'annotation en cours (réponse du modèle, relecture existante, ajouts à la main)
  const [quotes, setQuotes] = useState<string[]>([]);
  // Extrait à révéler dans le document : clic sur une citation du formulaire → scroll + flash.
  // `n` (nonce) relance le scroll même sur la même citation.
  const [focus, setFocus] = useState<{ quote: string; n: number } | null>(null);
  const [goldBusy, setGoldBusy] = useState(false);
  const [goldNote, setGoldNote] = useState<string | null>(null);

  // File des articles du projet : sert à ouvrir le premier, puis à enchaîner.
  useEffect(() => {
    if (!projectId) return;
    const load = async () => {
      // Filtres de la liste d'articles (mémorisés par l'onglet).
      let saved: { filter?: string; sort?: "added" | "date_desc" | "date_asc" | "score_desc"; answers?: Record<string, string | boolean>; q?: string; fulltext?: boolean } = {};
      try {
        saved = JSON.parse(sessionStorage.getItem(articlesStoreKey(projectId, searchId)) ?? "{}");
      } catch {
        /* pas de filtres mémorisés */
      }
      const status =
        queueMode === "review" ? "TO_REVIEW" : queueMode === "list" && saved.filter && saved.filter !== "ALL" ? (saved.filter as PredictionFilter) : undefined;
      const sort = queueMode === "list" ? (saved.sort ?? "added") : "added";
      const answers = queueMode === "all" ? {} : (saved.answers ?? {});
      let r = await listArticles(projectId, 1, 200, status, sort, answers, searchId, queueMode === "all" ? undefined : saved.q, queueMode === "all" ? undefined : saved.fulltext);
      // La liste est paginée par 200 : on poursuit jusqu'à trouver l'article ouvert.
      const ids = r.items.map((a) => a.id);
      for (let p = 2; articleId && !ids.includes(articleId) && ids.length < r.total && p <= 5; p++) {
        ids.push(...(await listArticles(projectId, p, 200, status, sort, answers, searchId, queueMode === "all" ? undefined : saved.q, queueMode === "all" ? undefined : saved.fulltext)).items.map((a) => a.id));
      }
      // Rien à relire (pas encore trié / tout validé) : on propose tous les articles.
      if (ids.length === 0 && queueMode === "review") r = await listArticles(projectId, 1, 200, undefined, "added", {}, searchId);
      return { ...r, items: ids.length ? ids.map((id) => ({ id })) : r.items };
    };
    load()
      .then((r) => {
        setQueue(r.items.map((a) => a.id));
        if (!articleId) {
          if (r.items.length > 0) {
            router.replace(
              withSearch(`/projects/${projectId}/annotate?article=${r.items[0].id}&queue=${queueMode}`, searchId)
            );
          } else {
            setLoading(false);
          }
        }
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, [projectId, articleId, router, queueMode, searchId]);

  useEffect(() => {
    if (!projectId || !articleId) return;
    setLoading(true);
    getProject(projectId, searchId)
      .then((p) => {
        setFields(p.fields ?? []);
        setSections(p.text_sections ?? ["methods"]);
      })
      .catch(() => {});
    getArticleWithPrediction(projectId, articleId, searchId)
      .then(({ article, prediction, review }) => {
        setArticle(article);
        setPrediction(prediction);
        setReview(review);
        setSaved(null);
        setGoldNote(null);
        setFocus(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [projectId, articleId, searchId]);

  // Swipe : droite = confirmer la réponse du modèle telle quelle ;
  // gauche = désaccord, on bascule sur le formulaire pour corriger.
  const confirmPrediction = useCallback(async () => {
    if (!articleId || !prediction?.decision_json) {
      setTab("annotation");
      return;
    }
    try {
      const r = await submitAnnotation(projectId, articleId, {
        ...prediction.decision_json,
        confidence_score: prediction.confidence_score ?? 0.5,
        evidence_quotes: prediction.evidence_quotes ?? [],
      }, searchId, isBlind);
      setReview({ ...r, decision_json: prediction.decision_json, notes: null });
      setStatus("accepted");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId, articleId, prediction, searchId, isBlind]);

  // Repart des extraits de la relecture, sinon de ceux du modèle (rien à l'aveugle avant relecture).
  useEffect(() => {
    const locked = isBlind && !review;
    const own = review?.decision_json?.evidence_quotes;
    setQuotes(
      Array.isArray(own)
        ? (own as string[])
        : review || locked
          ? []
          : prediction?.evidence_quotes ?? []
    );
  }, [articleId, review, prediction, isBlind]);

  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const handlePdf = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      setPdfBusy(true);
      setPdfError(null);
      try {
        try {
          setArticle(await uploadArticlePdf(projectId, articleId, file, false, searchId));
        } catch (e) {
          // Le PDF ne ressemble pas à cet article : on laisse l'utilisateur trancher.
          if (!(e instanceof Error && e.message === "pdf_mismatch")) throw e;
          if (!window.confirm(t("pdfMismatch"))) return;
          setArticle(await uploadArticlePdf(projectId, articleId, file, true, searchId));
        }
      } catch (e) {
        setPdfError((e as Error).message);
      } finally {
        setPdfBusy(false);
      }
    },
    [projectId, articleId, searchId, t]
  );

  const [searching, setSearching] = useState(false);
  const [searchNote, setSearchNote] = useState<string | null>(null);
  const handleSearch = useCallback(async () => {
    setSearching(true);
    setSearchNote(null);
    setPdfError(null);
    try {
      const r = await searchArticleFulltext(projectId, articleId, searchId);
      setArticle(r.article);
      setSearchNote(r.found ? t("searchFound", { source: r.source ?? "" }) : t("searchNone"));
    } catch (e) {
      setPdfError((e as Error).message);
    } finally {
      setSearching(false);
    }
  }, [projectId, articleId, searchId, t]);

  const handleSwipeRight = useCallback(() => {
    void confirmPrediction();
  }, [confirmPrediction]);
  const handleSwipeLeft = useCallback(() => {
    setTab("annotation");
  }, []);

  const handleSubmit = useCallback(
    async (annotation: Annotation) => {
      try {
        const r = await submitAnnotation(projectId, articleId, annotation, searchId, isBlind);
        setReview({ ...r, decision_json: annotation, notes: String(annotation.reasoning_summary ?? "") });
        setSaved(r.status ?? "MANUAL");
        setStatus("submitted");
        setGoldNote(null);
        // À l'aveugle, la réponse du modèle n'est révélée qu'une fois la relecture enregistrée.
        if (isBlind) setPrediction(await getPrediction(projectId, articleId, searchId).catch(() => null));
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [projectId, articleId, searchId, isBlind]
  );

  const addToGold = useCallback(async () => {
    setGoldBusy(true);
    setGoldNote(null);
    try {
      const r = await goldFromReviews(projectId, { articleIds: [articleId], refresh: true }, searchId);
      setGoldNote(r.added ? t("goldAdded") : r.updated ? t("goldUpdated") : t("goldSkipped"));
    } catch (e) {
      setGoldNote((e as Error).message);
    } finally {
      setGoldBusy(false);
    }
  }, [projectId, articleId, searchId, t]);

  if (loading || searches === null) return <div className="p-4">{tc("loading")}</div>;
  if (error) return <div className="p-4 text-danger">{error}</div>;
  if (!article)
    return (
      <div className="p-4 text-sm">
        {t("noArticles")}{" "}
        <Link href={withSearch(`/projects/${projectId}/import`, searchId)} className="text-primary hover:underline">
          {t("goImport")} →
        </Link>
      </div>
    );

  const position = queue.indexOf(articleId);
  const nextId = queue[position + 1] ?? null;
  const prevId = position > 0 ? queue[position - 1] : null;
  const goTo = (id: string) =>
    withSearch(`/projects/${projectId}/annotate?article=${id}&queue=${queueMode}`, searchId);
  const stepCls = (on: boolean) =>
    `rounded border px-3 py-1 text-sm ${
      on ? "border-border text-primary hover:bg-surface-muted" : "pointer-events-none border-border/50 text-muted/50"
    }`;
  const stepper =
    queue.length > 1 && position >= 0 ? (
      <nav aria-label={t("navArticles")} className="flex shrink-0 items-center gap-2">
        <Link href={prevId ? goTo(prevId) : "#"} aria-disabled={!prevId} className={stepCls(!!prevId)}>
          ← {t("previousArticle")}
        </Link>
        <span className="text-xs tabular-nums text-muted">
          {position + 1} / {queue.length}
        </span>
        <Link href={nextId ? goTo(nextId) : "#"} aria-disabled={!nextId} className={stepCls(!!nextId)}>
          {t("nextShort")} →
        </Link>
      </nav>
    ) : null;

  // À l'aveugle et pas encore relu : rien du modèle (ni réponses, ni preuves surlignées).
  const blindLocked = isBlind && !review;
  const shownPrediction = blindLocked ? null : prediction;

  // Une relecture existante prime sur la réponse de l'IA : on corrige à partir d'elle.
  const initialAnnotation = review?.decision_json
    ? { ...review.decision_json, field_confidence: shownPrediction?.decision_json?.field_confidence }
    : shownPrediction?.decision_json
    ? {
        ...shownPrediction.decision_json,
        confidence_score: shownPrediction.confidence_score ?? 0.5,
        evidence_quotes: shownPrediction.evidence_quotes ?? [],
      }
    : undefined;

  return (
    <div className="flex flex-col">
      {/* Barre supérieure (desktop) */}
      <header className="hidden border-b p-4 md:block">
        <div className="flex items-center justify-between gap-3">
          <Link
            href={withSearch(`/projects/${projectId}/articles`, searchId)}
            className="text-sm text-primary hover:underline"
          >
            ← {t("backToArticles")}
          </Link>
          {stepper}
        </div>
        <h1 className="mt-1 text-lg font-semibold">
          {article.title ?? `Article ${article.pmid ?? article.id}`}
        </h1>
        {biblioLine(article) && <p className="text-sm text-foreground/80">{biblioLine(article)}</p>}
        <p className="text-xs text-muted">
          {sourceLabel(article.source, article.native_id, ta("sourceFile")) ?? ta("sourceNotRecorded")}
        </p>
        <ArticleLinks article={article} className="mt-0.5" />
        {shownPrediction && (
          <p className="text-sm text-muted">
            {t("model")}:{" "}
            <ModelRef
              name={shownPrediction.model_used}
              identifier={shownPrediction.model_identifier}
              version={shownPrediction.model_version}
            />{" "}
            · {t("status")}:{" "}
            {shownPrediction.status}
          </p>
        )}
      </header>

      {/* Barre supérieure (mobile) : retour aux articles */}
      <header className="flex items-center justify-between gap-2 border-b p-3 md:hidden">
        <Link
          href={withSearch(`/projects/${projectId}/articles`, searchId)}
          className="text-sm text-primary hover:underline"
        >
          ← {t("backToArticles")}
        </Link>
        {stepper}
      </header>

      {isBlind && (
        <div className="border-b border-warning/40 bg-warning/10 p-3 text-sm text-foreground" role="status">
          <strong>{t("blindBanner")}</strong>{" "}
          {blindLocked ? t("blindHidden") : t("blindRevealed")}
        </div>
      )}

      {/* Corps : double colonne (desktop) / mono-colonne à onglets (mobile) */}
      <main className="flex flex-col md:h-[calc(100vh_-_14rem)] md:flex-row md:overflow-hidden">
        {/* Document (gauche sur desktop, onglet sur mobile) */}
        <section
          className={`max-h-[70vh] min-h-0 flex-[3] overflow-y-auto p-4 md:max-h-none ${
            tab === "document" ? "block" : "hidden md:block"
          }`}
        >
          <SwipeableCard
            onSwipeRight={handleSwipeRight}
            onSwipeLeft={handleSwipeLeft}
            className="h-full"
          >
            <h2 className="mb-2 text-sm font-medium text-muted">
              {sections.includes("all") ? ti("kind_all") : sections.map((k) => ti(`kind_${k}`)).join(" · ")}
            </h2>
            <div
              className={`mb-2 rounded border p-2 text-xs ${
                article.has_fulltext ? "border-border" : "border-warning/40 bg-warning/10"
              }`}
            >
              {!article.has_fulltext && (
                <p className="text-foreground">
                  {article.fulltext_note === "preprint_not_archived" ? t("preprintNote") : t("abstractOnlyNote")}
                </p>
              )}
              <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1">
                <button
                  type="button"
                  className="font-medium text-primary hover:underline disabled:opacity-50"
                  disabled={searching || pdfBusy}
                  onClick={() => void handleSearch()}
                >
                  {searching ? t("searching") : t("searchFulltext")}
                </button>
                <label className="cursor-pointer font-medium text-primary hover:underline">
                  {pdfBusy ? t("pdfBusy") : article.has_fulltext ? t("replacePdf") : t("addPdf")}
                  <input
                    type="file"
                    accept="application/pdf"
                    className="hidden"
                    disabled={pdfBusy || searching}
                    onChange={(e) => void handlePdf(e.target.files?.[0])}
                  />
                </label>
                {searchNote && <span className="text-muted">{searchNote}</span>}
                {pdfError && <span className="text-danger">{pdfError}</span>}
              </div>
            </div>
            {shownPrediction && !shownPrediction.evidence_quotes?.length && (
              <p className="mb-2 rounded bg-surface-muted p-2 text-xs text-muted">{t("noEvidence")}</p>
            )}
            <EvidenceHighlighter
              text={article.text ?? article.methods_text ?? ""}
              quotes={quotes}
              onAdd={(q) => setQuotes((prev) => addQuote(prev, q))}
              focus={focus}
            />
            {status !== "idle" && (
              <div className="mt-4">
                <p
                  className={`rounded p-2 text-sm ${
                    status === "accepted"
                      ? "bg-success/15 text-success"
                      : status === "rejected"
                        ? "bg-danger/15 text-danger"
                        : "bg-primary/15 text-primary"
                  }`}
                >
                  {status === "accepted" && t("accepted")}
                  {status === "rejected" && t("rejected")}
                  {status === "submitted" && t(saved ? `saved_${saved}` : "submitted")}
                </p>
                {nextId ? (
                  <Link
                    href={withSearch(`/projects/${projectId}/annotate?article=${nextId}&queue=${queueMode}`, searchId)}
                    className="mt-2 inline-block text-sm font-medium text-primary hover:underline"
                  >
                    {t("nextArticle")} →
                  </Link>
                ) : (
                  <Link
                    href={withSearch(`/projects/${projectId}/articles`, searchId)}
                    className="mt-2 inline-block text-sm font-medium text-primary hover:underline"
                  >
                    {t("backToArticles")} →
                  </Link>
                )}
              </div>
            )}
          </SwipeableCard>
        </section>

        {/* Formulaire (droite sur desktop, onglet sur mobile) */}
        <section
          className={`max-h-[70vh] min-h-0 flex-[2] overflow-y-auto border-t p-4 md:max-h-none md:border-l md:border-t-0 ${
            tab === "annotation" ? "block" : "hidden md:block"
          }`}
        >
          <h2 className="mb-2 text-sm font-medium text-muted">
            {t("annotation")}
          </h2>
          {review && (
            <p className="mb-3 rounded border border-success/40 bg-success/10 p-2 text-xs text-foreground">
              ✓{" "}
              {t("reviewDone", {
                name: review.by ?? "?",
                date: review.at ? new Date(review.at).toLocaleString(locale) : "",
              })}
              {review.status && ` — ${t(`reviewStatus_${review.status}`)}`}
              <span className="mt-1 block text-muted">{t("reviewEditHint")}</span>
              <span className="mt-2 flex flex-wrap items-center gap-2">
                <Button type="button" size="sm" variant="secondary" loading={goldBusy} onClick={() => void addToGold()}>
                  {t("addToGold")}
                </Button>
                {goldNote && (
                  <span className="text-foreground" role="status">
                    {goldNote}
                  </span>
                )}
              </span>
            </p>
          )}
          <AnnotationForm
            key={`${fields.map((f) => f.key).join(",")}:${review?.at ?? ""}`}
            fields={fields}
            onSubmit={handleSubmit}
            initial={initialAnnotation}
            quotes={quotes}
            onQuotesChange={setQuotes}
            onQuoteClick={(q) => {
              setFocus((prev) => ({ quote: q, n: (prev?.n ?? 0) + 1 }));
              setTab("document"); // mobile : le document est sous un onglet
            }}
          />
        </section>
      </main>

      {/* Barre de navigation inférieure (mobile) */}
      <nav className="flex border-t md:hidden">
        <button
          onClick={() => setTab("document")}
          className={`flex-1 py-3 text-sm ${
            tab === "document" ? "font-semibold text-primary" : "text-muted"
          }`}
        >
          {t("document")}
        </button>
        <button
          onClick={() => setTab("annotation")}
          className={`flex-1 py-3 text-sm ${
            tab === "annotation" ? "font-semibold text-primary" : "text-muted"
          }`}
        >
          {t("annotation")}
        </button>
      </nav>
    </div>
  );
}
