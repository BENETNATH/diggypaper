"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArticleLinks, biblioLine } from "@/components/Certainty";
import { AnnotationForm, Annotation } from "@/components/AnnotationForm";
import { EvidenceHighlighter } from "@/components/EvidenceHighlighter";
import { SwipeableCard } from "@/components/SwipeableCard";
import {
  Article,
  ArticlePrediction,
  ArticleReview,
  getArticleWithPrediction,
  getProject,
  listArticles,
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
  const tc = useTranslations("common");
  const locale = useLocale();
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const projectId = params.id;
  const articleId = searchParams.get("article") ?? "";
  // queue=review : seulement les cas à relire (défaut après un tri) ; all : tous les articles.
  const queueMode = searchParams.get("queue") ?? "review";

  const [article, setArticle] = useState<Article | null>(null);
  const [prediction, setPrediction] = useState<ArticlePrediction | null>(null);
  const [review, setReview] = useState<ArticleReview | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"document" | "annotation">("document");
  const router = useRouter();
  const [fields, setFields] = useState<SchemaField[]>([]);
  const [queue, setQueue] = useState<string[]>([]);
  const [status, setStatus] = useState<
    "idle" | "accepted" | "rejected" | "submitted"
  >("idle");

  // File des articles du projet : sert à ouvrir le premier, puis à enchaîner.
  useEffect(() => {
    if (!projectId) return;
    const load = async () => {
      let r = await listArticles(
        projectId,
        1,
        200,
        queueMode === "review" ? "TO_REVIEW" : undefined
      );
      // Rien à relire (pas encore trié / tout validé) : on propose tous les articles.
      if (r.items.length === 0 && queueMode === "review") r = await listArticles(projectId, 1, 200);
      return r;
    };
    load()
      .then((r) => {
        setQueue(r.items.map((a) => a.id));
        if (!articleId) {
          if (r.items.length > 0) {
            router.replace(
              `/projects/${projectId}/annotate?article=${r.items[0].id}&queue=${queueMode}`
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
  }, [projectId, articleId, router, queueMode]);

  useEffect(() => {
    if (!projectId || !articleId) return;
    setLoading(true);
    getProject(projectId)
      .then((p) => setFields(p.fields ?? []))
      .catch(() => {});
    getArticleWithPrediction(projectId, articleId)
      .then(({ article, prediction, review }) => {
        setArticle(article);
        setPrediction(prediction);
        setReview(review);
        setSaved(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [projectId, articleId]);

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
      });
      setReview({ ...r, decision_json: prediction.decision_json, notes: null });
      setStatus("accepted");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [projectId, articleId, prediction]);

  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const handlePdf = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      setPdfBusy(true);
      setPdfError(null);
      try {
        try {
          setArticle(await uploadArticlePdf(projectId, articleId, file));
        } catch (e) {
          // Le PDF ne ressemble pas à cet article : on laisse l'utilisateur trancher.
          if (!(e instanceof Error && e.message === "pdf_mismatch")) throw e;
          if (!window.confirm(t("pdfMismatch"))) return;
          setArticle(await uploadArticlePdf(projectId, articleId, file, true));
        }
      } catch (e) {
        setPdfError((e as Error).message);
      } finally {
        setPdfBusy(false);
      }
    },
    [projectId, articleId, t]
  );

  const [searching, setSearching] = useState(false);
  const [searchNote, setSearchNote] = useState<string | null>(null);
  const handleSearch = useCallback(async () => {
    setSearching(true);
    setSearchNote(null);
    setPdfError(null);
    try {
      const r = await searchArticleFulltext(projectId, articleId);
      setArticle(r.article);
      setSearchNote(r.found ? t("searchFound", { source: r.source ?? "" }) : t("searchNone"));
    } catch (e) {
      setPdfError((e as Error).message);
    } finally {
      setSearching(false);
    }
  }, [projectId, articleId, t]);

  const handleSwipeRight = useCallback(() => {
    void confirmPrediction();
  }, [confirmPrediction]);
  const handleSwipeLeft = useCallback(() => {
    setTab("annotation");
  }, []);

  const handleSubmit = useCallback(
    async (annotation: Annotation) => {
      try {
        const r = await submitAnnotation(projectId, articleId, annotation);
        setReview({ ...r, decision_json: annotation, notes: String(annotation.reasoning_summary ?? "") });
        setSaved(r.status ?? "MANUAL");
        setStatus("submitted");
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [projectId, articleId]
  );

  if (loading) return <div className="p-4">{tc("loading")}</div>;
  if (error) return <div className="p-4 text-danger">{error}</div>;
  if (!article)
    return (
      <div className="p-4 text-sm">
        {t("noArticles")}{" "}
        <Link href={`/projects/${projectId}/import`} className="text-primary hover:underline">
          {t("goImport")} →
        </Link>
      </div>
    );

  const nextId = queue[queue.indexOf(articleId) + 1] ?? null;

  // Une relecture existante prime sur la réponse de l'IA : on corrige à partir d'elle.
  const initialAnnotation = review?.decision_json
    ? { ...review.decision_json, field_confidence: prediction?.decision_json?.field_confidence }
    : prediction?.decision_json
    ? {
        ...prediction.decision_json,
        confidence_score: prediction.confidence_score ?? 0.5,
        evidence_quotes: prediction.evidence_quotes ?? [],
      }
    : undefined;

  return (
    <div className="flex flex-col">
      {/* Barre supérieure (desktop) */}
      <header className="hidden border-b p-4 md:block">
        <Link
          href={`/projects/${projectId}/articles`}
          className="text-sm text-primary hover:underline"
        >
          ← {t("backToArticles")}
        </Link>
        <h1 className="mt-1 text-lg font-semibold">
          {article.title ?? `Article ${article.pmid ?? article.id}`}
        </h1>
        {biblioLine(article) && <p className="text-sm text-foreground/80">{biblioLine(article)}</p>}
        <ArticleLinks article={article} className="mt-0.5" />
        {prediction && (
          <p className="text-sm text-muted">
            {t("model")}: {prediction.model_used} · {t("status")}:{" "}
            {prediction.status}
          </p>
        )}
      </header>

      {/* Barre supérieure (mobile) : retour aux articles */}
      <header className="border-b p-3 md:hidden">
        <Link
          href={`/projects/${projectId}/articles`}
          className="text-sm text-primary hover:underline"
        >
          ← {t("backToArticles")}
        </Link>
      </header>

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
              {t("methods")}
            </h2>
            <div
              className={`mb-2 rounded border p-2 text-xs ${
                article.has_fulltext ? "border-border" : "border-warning/40 bg-warning/10"
              }`}
            >
              {!article.has_fulltext && <p className="text-foreground">{t("abstractOnlyNote")}</p>}
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
            {prediction && !prediction.evidence_quotes?.length && (
              <p className="mb-2 rounded bg-surface-muted p-2 text-xs text-muted">{t("noEvidence")}</p>
            )}
            <EvidenceHighlighter
              text={article.text ?? article.methods_text ?? ""}
              quotes={prediction?.evidence_quotes}
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
                    href={`/projects/${projectId}/annotate?article=${nextId}&queue=${queueMode}`}
                    className="mt-2 inline-block text-sm font-medium text-primary hover:underline"
                  >
                    {t("nextArticle")} →
                  </Link>
                ) : (
                  <Link
                    href={`/projects/${projectId}/articles`}
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
            </p>
          )}
          <AnnotationForm
            key={`${fields.map((f) => f.key).join(",")}:${review?.at ?? ""}`}
            fields={fields}
            onSubmit={handleSubmit}
            initial={initialAnnotation}
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
