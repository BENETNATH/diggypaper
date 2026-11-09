"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Article, SchemaField } from "@/lib/api";
import { ArticleLinks, biblioLine } from "@/components/Certainty";
import { cellIcon, cellTone, deriveHumanFieldState, initials } from "@/lib/resultCells";
import { sourceLabel } from "@/lib/source";

export const STATUS_DOT: Record<string, string> = {
  AUTO_ACCEPTED: "bg-success",
  AUTO_REJECTED: "bg-danger",
  NEEDS_HUMAN_REVIEW: "bg-warning",
  ERROR: "bg-danger",
};

/** Technical value ("BOTH_SEXES") → "both sexes"; free text stays as-is. */
const pretty = (v: unknown): string => {
  const text = String(v);
  return /^[A-Z0-9_]+$/.test(text) ? text.replace(/_/g, " ").toLowerCase() : text;
};

/** Small certainty dot (green / orange / red); the percentage is in the tooltip. */
function CertaintyDot({ value }: { value: number | null | undefined }) {
  const t = useTranslations("articles");
  if (value == null) return null;
  const pct = Math.round(value * 100);
  const tone = pct >= 85 ? "bg-success" : pct >= 60 ? "bg-warning" : "bg-danger";
  return <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${tone}`} title={`${t("certaintyHelp")} : ${pct} %`} aria-label={`${pct} %`} />;
}

type Answer = NonNullable<Article["answers"]>[number];

/** Background of the colored cell (spec 2026-11-09-triage-seuils R3, mockup 04). */
const CELL_TONE_BG: Record<ReturnType<typeof cellTone>, string> = {
  good: "bg-cell-good",
  warn: "bg-cell-warn",
  error: "bg-surface-muted",
  neutral: "bg-surface-muted/40",
};

/**
 * An answer in a fixed-size box: the item (one line) and the answer (at most two lines)
 * are truncated, the certainty pill stays always visible; on hover (or keyboard), a
 * card shows the item, the full answer and its origin.
 *
 * Colored background (R3, opt-in per question) + corner human-review icon with
 * tooltip (R4); the pill is kept as the non-colored channel (WCAG 1.4.1, R5).
 */
export function AnswerCell({
  r,
  accept,
  badgeOn = false,
  conf = null,
  humanState = "none",
  annotator = null,
  modelValue,
  rowError = false,
}: {
  r: Answer;
  /** Project accept threshold (null = family disabled). */
  accept?: number | null;
  /** "badge" option of the question (colored cell). */
  badgeOn?: boolean;
  /** Per-field confidence of the last prediction (null: old predictions, R2). */
  conf?: number | null;
  /** Human review state of this field (derived, R4). */
  humanState?: "validated" | "corrected" | "none";
  /** Last annotator, for the tooltip initials. */
  annotator?: string | null;
  /** The model's own answer for the field (empty-cell check). */
  modelValue?: unknown;
  rowError?: boolean;
}) {
  const t = useTranslations("articles");
  const yes = r.value === true;
  const no = r.value === false;
  const shown = typeof r.value === "boolean" ? (yes ? `✓ ${t("yes")}` : `✗ ${t("no")}`) : pretty(r.value);
  const muted = no || r.value === "UNSPECIFIED" || shown === "";
  const pct = r.confidence != null ? Math.round(r.confidence * 100) : null;
  const tone = cellTone({
    rowError,
    badgeOn,
    fieldConfidence: conf,
    answerPresent: r.value != null,
    accept,
  });
  const icon = cellIcon({ rowError, modelValue, humanState });
  const iconTitle =
    icon === "validated"
      ? t("cellValidated", { name: initials(annotator) }) +
        (conf != null ? ` · ${t("cellAi", { pct: Math.round(conf * 100) })}` : "")
      : icon === "corrected"
        ? t("cellCorrected", { name: initials(annotator) }) +
          (conf != null ? ` — ${t("cellAi", { pct: Math.round(conf * 100) })}` : "")
        : t("cellUnread");
  // Not focusable: the row link is the only focus stop; the tooltip opens on the link's focus (group/row).
  return (
    <div className="group/cell relative">
      <div className={`h-[3.4rem] overflow-hidden rounded-md px-2.5 py-1.5 ${CELL_TONE_BG[tone]}`}>
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-[11px] text-muted">{r.label}</span>
          <span className="flex shrink-0 items-center gap-1.5">
            {icon !== "none" && (
              <span
                title={iconTitle}
                aria-label={iconTitle}
                className={`text-xs ${
                  icon === "validated" ? "text-success" : icon === "corrected" ? "text-warning" : "text-muted"
                }`}
              >
                {icon === "validated" ? "✓" : icon === "corrected" ? "!" : "·"}
              </span>
            )}
            {r.source === "human" ? (
              <span title={t("humanAnswer")} className="text-xs text-primary">
                ✎
              </span>
            ) : (
              <CertaintyDot value={r.confidence} />
            )}
          </span>
        </div>
        <p className={`mt-0.5 line-clamp-2 break-words text-xs leading-snug ${muted ? "text-muted" : "text-foreground"}`}>
          {shown || "—"}
        </p>
      </div>
      {/* No gap between the cell and the card (pt-1, not mt-1) and active pointer: we can
          move the mouse there and scroll it; a slight delay prevents it from closing en route. */}
      <div
        role="tooltip"
        onClick={(e) => {
          e.preventDefault(); // the row is a link: a click in the card does not open the article
          e.stopPropagation();
        }}
        className="invisible absolute left-0 top-full z-30 w-80 max-w-[min(20rem,calc(100vw-3rem))] pt-1 opacity-0 transition-[opacity,visibility] delay-150 group-focus/row:visible group-focus/row:opacity-100 group-focus/row:delay-0 group-hover/cell:visible group-hover/cell:opacity-100 group-hover/cell:delay-0"
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
 * A row of the results list: title, one line of grey markers, answers in
 * fixed-size boxes, and on the right the state: re-reviewed or the model's decision.
 * When `canDelete`, a checkbox (bulk select) and a delete button are added.
 */
export function ResultRow({
  a,
  href,
  fields,
  accept,
  canDelete = false,
  selected,
  onToggleSelect,
  onDelete,
}: {
  a: Article;
  href: string;
  /** The search's fields (badge option per question, R3). */
  fields?: SchemaField[];
  /** Project accept threshold (R3). */
  accept?: number | null;
  canDelete?: boolean;
  selected?: Set<string>;
  onToggleSelect?: (id: string) => void;
  onDelete?: (id: string) => void;
}) {
  const t = useTranslations("articles");
  const meta = [
    biblioLine(a),
    sourceLabel(a.source, a.native_id, t("sourceFile")) ?? a.doi ?? "",
    a.has_fulltext
      ? t("fulltext")
      : a.fulltext_note === "no_text_layer"
        ? t("noTextLayer")
        : t("abstractOnly"),
    a.origin === "citation" ? t("originCitation", { n: a.origin_seeds ?? 0 }) : "",
  ].filter(Boolean);
  const answers = a.answers ?? [];
  const reviewed = a.annotated;
  const isSelected = selected?.has(a.id) ?? false;
  const rowError = a.prediction_status === "ERROR";
  const badgeOf = (key: string) => fields?.find((f) => f.key === key)?.badge === true;
  return (
    <li className="group">
      <div className="flex items-start gap-2.5">
        {canDelete && (
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => onToggleSelect?.(a.id)}
            onClick={(e) => e.stopPropagation()}
            aria-label={t("selectArticle")}
            className={`mt-3.5 h-4 w-4 shrink-0 rounded border-border ${
              isSelected ? "bg-primary text-surface" : "bg-surface"
            }`}
          />
        )}
        <div className="min-w-0 flex-1">
          <Link
            href={href}
            className="group/row flex flex-col gap-2 rounded-lg px-3 py-3 transition-colors hover:bg-surface-muted/60 sm:flex-row sm:items-start sm:justify-between sm:gap-6"
          >
            <div className="min-w-0 flex-1">
              <p className="line-clamp-2 text-[15px] leading-snug text-foreground">{a.title ?? t("untitled")}</p>
              <p className="mt-1 truncate text-xs text-muted">{meta.join(" · ")}</p>
              {answers.length > 0 && (
                <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {answers.map((r) => (
                    <AnswerCell
                      key={r.key}
                      r={r}
                      accept={accept}
                      badgeOn={badgeOf(r.key)}
                      conf={a.field_confidences?.[r.key] ?? null}
                      humanState={deriveHumanFieldState(a.model_json, a.human?.decision_json, r.key)}
                      annotator={a.human?.annotator ?? null}
                      modelValue={a.model_json?.[r.key]}
                      rowError={rowError}
                    />
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
              {canDelete && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onDelete?.(a.id);
                  }}
                  aria-label={t("delete")}
                  title={t("delete")}
                  className="mt-0.5 rounded p-1 text-muted opacity-0 transition-opacity hover:text-danger focus-visible:opacity-100 group-hover/row:opacity-100"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4" aria-hidden>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              )}
            </div>
          </Link>
          <ArticleLinks article={a} className="px-3 pb-2 -mt-1" />
        </div>
      </div>
    </li>
  );
}
