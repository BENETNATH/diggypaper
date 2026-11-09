"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Article, SchemaField } from "@/lib/api";
import { AnswerCell, STATUS_DOT } from "@/components/ResultRow";
import { deriveHumanFieldState } from "@/lib/resultCells";
import { biblioLine } from "@/components/Certainty";

/**
 * Results read as a table — one column per question (spec 2026-11-06-ux-refonte-v4 R3).
 * Each cell is the existing AnswerCell: answer truncated, full answer on hover,
 * certainty dot kept. Used when the analysis has ≥ 2 questions.
 */
export function ResultsTable({
  articles,
  fields,
  accept,
  hrefFor,
  canDelete = false,
  selected,
  onToggleSelect,
  onDelete,
}: {
  articles: Article[];
  fields: SchemaField[];
  /** Project accept threshold (colored cells, R3). */
  accept?: number | null;
  hrefFor: (a: Article) => string;
  canDelete?: boolean;
  selected?: Set<string>;
  onToggleSelect?: (id: string) => void;
  onDelete?: (id: string) => void;
}) {
  const t = useTranslations("articles");
  // The table can be wider than its card (one column per question, min widths):
  // scroll horizontally inside the card, never outside the page background.
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] border-collapse text-sm">
      <thead>
        <tr className="border-b border-border">
          {canDelete && <th className="w-8 px-2 py-2" aria-label={t("selectArticle")} />}
          <th className="px-3 py-2 text-left text-xs font-medium text-muted">{t("tableArticle")}</th>
          {fields.map((f) => (
            <th key={f.key} className="min-w-[11rem] px-3 py-2 text-left text-xs font-medium text-muted" title={f.label}>
              {f.label}
            </th>
          ))}
          <th className="px-3 py-2 text-left text-xs font-medium text-muted">{t("tableStatus")}</th>
        </tr>
      </thead>
      <tbody>
        {articles.map((a) => (
          <tr key={a.id} className="group/row border-b border-border/60 align-top">
            {canDelete && (
              <td className="px-2 py-2">
                <input
                  type="checkbox"
                  checked={selected?.has(a.id) ?? false}
                  onChange={() => onToggleSelect?.(a.id)}
                  aria-label={t("selectArticle")}
                  className={`h-4 w-4 rounded border-border ${
                    (selected?.has(a.id) ?? false) ? "bg-primary text-surface" : "bg-surface"
                  }`}
                />
              </td>
            )}
            <td className="px-3 py-2">
              <div className="flex items-start gap-1">
                <Link href={hrefFor(a)} className="min-w-0 text-foreground hover:text-primary">
                  <span className="line-clamp-2 text-[13px] leading-snug">{a.title ?? t("untitled")}</span>
                </Link>
                {canDelete && (
                  <button
                    type="button"
                    onClick={() => onDelete?.(a.id)}
                    aria-label={t("delete")}
                    title={t("delete")}
                    className="mt-0.5 shrink-0 rounded p-0.5 text-muted opacity-0 transition-opacity hover:text-danger focus-visible:opacity-100 group-hover/row:opacity-100"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4" aria-hidden>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                )}
              </div>
              <p className="mt-0.5 truncate text-[11px] text-muted">{biblioLine(a)}</p>
            </td>
            {fields.map((f) => {
              const ans = a.answers?.find((r) => r.key === f.key);
              return (
                <td key={f.key} className="px-2 py-2">
                  {ans ? (
                    <AnswerCell
                      r={ans}
                      accept={accept}
                      badgeOn={f.badge === true}
                      conf={a.field_confidences?.[f.key] ?? null}
                      humanState={deriveHumanFieldState(a.model_json, a.human?.decision_json, f.key)}
                      annotator={a.human?.annotator ?? null}
                      modelValue={a.model_json?.[f.key]}
                      rowError={a.prediction_status === "ERROR"}
                    />
                  ) : (
                    <span className="text-xs text-muted">—</span>
                  )}
                </td>
              );
            })}
            <td className="whitespace-nowrap px-3 py-2 text-xs">
              {a.prediction_status && (
                <span className="inline-flex items-center gap-1.5 text-foreground">
                  <span className={`h-2 w-2 rounded-full ${STATUS_DOT[a.prediction_status] ?? "bg-border"}`} aria-hidden />
                  {t(`status_${a.prediction_status}`)}
                </span>
              )}
              {a.annotated && a.review?.status && (
                <span className={a.review.status === "CORRECTED" ? "text-warning" : "text-success"}>
                  ✓ {t(`review_${a.review.status}`)}
                  {a.review.by ? <span className="text-muted"> · {a.review.by}</span> : null}
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
      </table>
      {fields.some((f) => f.badge) && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 px-3 py-2 text-[11px] text-muted">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-3 w-3 rounded bg-cell-good" aria-hidden />
            {t("legendGood")}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-3 w-3 rounded bg-cell-warn" aria-hidden />
            {t("legendWarn")}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-3 w-3 rounded border border-border bg-surface-muted/40" aria-hidden />
            {t("legendNeutral")}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-3 w-3 rounded bg-surface-muted" aria-hidden />
            {t("legendError")}
          </span>
        </div>
      )}
    </div>
  );
}
