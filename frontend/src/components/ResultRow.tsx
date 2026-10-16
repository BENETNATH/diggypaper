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

/** Valeur technique (« BOTH_SEXES ») → « both sexes » ; un texte libre reste tel quel. */
const pretty = (v: unknown): string => {
  const text = String(v);
  return /^[A-Z0-9_]+$/.test(text) ? text.replace(/_/g, " ").toLowerCase() : text;
};

/** Petit point de certitude (vert / orange / rouge) ; le pourcentage est dans l'infobulle. */
function CertaintyDot({ value }: { value: number | null | undefined }) {
  const t = useTranslations("articles");
  if (value == null) return null;
  const pct = Math.round(value * 100);
  const tone = pct >= 85 ? "bg-success" : pct >= 60 ? "bg-warning" : "bg-danger";
  return <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${tone}`} title={`${t("certaintyHelp")} : ${pct} %`} aria-label={`${pct} %`} />;
}

type Answer = NonNullable<Article["answers"]>[number];

/**
 * Une réponse dans un cadre de taille fixe : l'item (une ligne) et la réponse (deux lignes au plus)
 * sont tronqués, la pastille de certitude reste toujours visible ; au survol (ou au clavier), une
 * fiche affiche l'item, la réponse complète et son origine.
 */
function AnswerCell({ r }: { r: Answer }) {
  const t = useTranslations("articles");
  const yes = r.value === true;
  const no = r.value === false;
  const shown = typeof r.value === "boolean" ? (yes ? `✓ ${t("yes")}` : `✗ ${t("no")}`) : pretty(r.value);
  const muted = no || r.value === "UNSPECIFIED" || shown === "";
  const pct = r.confidence != null ? Math.round(r.confidence * 100) : null;
  return (
    <div className="group/cell relative" tabIndex={0}>
      <div className="h-[3.4rem] overflow-hidden rounded-md border border-border/70 bg-surface-muted/40 px-2.5 py-1.5">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-[11px] text-muted">{r.label}</span>
          {r.source === "human" ? (
            <span title={t("humanAnswer")} className="shrink-0 text-xs text-primary">
              ✎
            </span>
          ) : (
            <CertaintyDot value={r.confidence} />
          )}
        </div>
        <p className={`mt-0.5 line-clamp-2 break-words text-xs leading-snug ${muted ? "text-muted" : "text-foreground"}`}>
          {shown || "—"}
        </p>
      </div>
      {/* Pas d'interstice entre la cellule et la fiche (pt-1, pas mt-1) et pointeur actif : on peut
          y amener la souris et la faire défiler ; un léger délai évite qu'elle se ferme en route. */}
      <div
        role="tooltip"
        onClick={(e) => {
          e.preventDefault(); // la ligne est un lien : un clic dans la fiche n'ouvre pas l'article
          e.stopPropagation();
        }}
        className="invisible absolute left-0 top-full z-30 w-80 max-w-[min(20rem,calc(100vw-3rem))] pt-1 opacity-0 transition-[opacity,visibility] delay-150 group-focus-within/cell:visible group-focus-within/cell:opacity-100 group-focus-within/cell:delay-0 group-hover/cell:visible group-hover/cell:opacity-100 group-hover/cell:delay-0"
      >
        <div className="rounded-lg border border-border bg-surface p-3 text-xs shadow-lg">
          <p className="text-muted">{r.label}</p>
          <p className="mt-1 max-h-48 overflow-y-auto overscroll-contain whitespace-pre-wrap break-words text-sm text-foreground">
            {typeof r.value === "boolean" || r.value == null ? shown || "—" : String(r.value)}
          </p>
          <p className="mt-2 text-muted">
            {r.source === "human" ? t("humanAnswer") : pct != null ? `${t("certaintyHelp")} : ${pct} %` : ""}
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * Une ligne de la liste des résultats : titre, une seule ligne de repères en gris, réponses en
 * cadres de taille fixe, et à droite l'état : relu ou décision du modèle.
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
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {answers.map((r) => (
                <AnswerCell key={r.key} r={r} />
              ))}
            </div>
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
