"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Article } from "@/lib/api";
import { ArticleLinks, biblioLine } from "@/components/Certainty";

const STATUS_DOT: Record<string, string> = {
  AUTO_ACCEPTED: "bg-success",
  AUTO_REJECTED: "bg-danger",
  NEEDS_HUMAN_REVIEW: "bg-warning",
};

/** « BOTH_SEXES » → « both sexes » ; les libellés trop longs sont tronqués (texte complet en infobulle). */
const pretty = (v: unknown): string => String(v).replace(/_/g, " ").toLowerCase();

/** Petit point de certitude (vert / orange / rouge) ; le pourcentage est dans l'infobulle. */
function CertaintyDot({ value }: { value: number | null | undefined }) {
  const t = useTranslations("articles");
  if (value == null) return null;
  const pct = Math.round(value * 100);
  const tone = pct >= 85 ? "bg-success" : pct >= 60 ? "bg-warning" : "bg-danger";
  return <span className={`inline-block h-1.5 w-1.5 rounded-full ${tone}`} title={`${t("certaintyHelp")} : ${pct} %`} aria-label={`${pct} %`} />;
}

/**
 * Une ligne de la liste des résultats : titre, une seule ligne de repères en gris, réponses en
 * texte courant (sans gras ni pastilles), et à droite l'état : relu ou décision du modèle.
 */
export function ResultRow({ a, href }: { a: Article; href: string }) {
  const t = useTranslations("articles");
  const meta = [
    biblioLine(a),
    a.pmid ? `PMID ${a.pmid}` : (a.doi ?? ""),
    a.has_fulltext ? t("fulltext") : t("abstractOnly"),
    a.origin === "citation" ? t("originCitation", { n: a.origin_seeds ?? 0 }) : "",
  ].filter(Boolean);
  const answers = a.answers ?? [];
  const reviewed = a.annotated;
  return (
    <li className="group">
      <Link
        href={href}
        className="flex flex-col gap-2 rounded-lg px-3 py-3 transition-colors hover:bg-surface-muted/60 sm:flex-row sm:items-start sm:justify-between sm:gap-6"
      >
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-[15px] leading-snug text-foreground">{a.title ?? t("untitled")}</p>
          <p className="mt-1 truncate text-xs text-muted">{meta.join(" · ")}</p>
          {answers.length > 0 && (
            <p className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
              {answers.map((r) => {
                const yes = r.value === true;
                const no = r.value === false;
                return (
                  <span key={r.key} className="inline-flex items-center gap-1.5">
                    <span className="max-w-[14rem] truncate" title={r.label}>
                      {r.label}
                    </span>
                    <span className={no || r.value === "UNSPECIFIED" ? "text-muted" : "text-foreground"}>
                      {typeof r.value === "boolean" ? (yes ? `✓ ${t("yes")}` : `✗ ${t("no")}`) : pretty(r.value)}
                    </span>
                    {r.source === "human" ? (
                      <span title={t("humanAnswer")} className="text-primary">
                        ✎
                      </span>
                    ) : (
                      <CertaintyDot value={r.confidence} />
                    )}
                  </span>
                );
              })}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col gap-1 text-xs sm:items-end">
          {a.prediction_status && (
            <span className="inline-flex items-center gap-1.5 text-foreground">
              <span className={`h-2 w-2 rounded-full ${STATUS_DOT[a.prediction_status] ?? "bg-border"}`} aria-hidden />
              {t(`status_${a.prediction_status}`)}
              {a.confidence != null && (
                <span className="text-muted" title={t("relevanceHelp")}>
                  · {Math.round(a.confidence * 100)} %
                </span>
              )}
            </span>
          )}
          {reviewed && (
            <span className={a.review?.status === "CORRECTED" ? "text-warning" : "text-success"}>
              ✓ {a.review?.status ? t(`review_${a.review.status}`) : t("reviewed")}
              {a.review?.by ? <span className="text-muted"> · {a.review.by}</span> : null}
            </span>
          )}
        </div>
      </Link>
      <ArticleLinks article={a} className="px-3 pb-2 -mt-1" />
    </li>
  );
}
