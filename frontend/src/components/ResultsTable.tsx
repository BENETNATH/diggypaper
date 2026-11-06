"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Article, SchemaField } from "@/lib/api";
import { AnswerCell, STATUS_DOT } from "@/components/ResultRow";
import { biblioLine } from "@/components/Certainty";

/**
 * Results read as a table — one column per question (spec 2026-11-06-ux-refonte-v4 R3).
 * Each cell is the existing AnswerCell: answer truncated, full answer on hover,
 * certainty dot kept. Used when the analysis has ≥ 2 questions.
 */
export function ResultsTable({
  articles,
  fields,
  hrefFor,
}: {
  articles: Article[];
  fields: SchemaField[];
  hrefFor: (a: Article) => string;
}) {
  const t = useTranslations("articles");
  // The table can be wider than its card (one column per question, min widths):
  // scroll horizontally inside the card, never outside the page background.
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] border-collapse text-sm">
      <thead>
        <tr className="border-b border-border">
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
          <tr key={a.id} className="border-b border-border/60 align-top">
            <td className="px-3 py-2">
              <Link href={hrefFor(a)} className="text-foreground hover:text-primary">
                <span className="line-clamp-2 text-[13px] leading-snug">{a.title ?? t("untitled")}</span>
              </Link>
              <p className="mt-0.5 truncate text-[11px] text-muted">{biblioLine(a)}</p>
            </td>
            {fields.map((f) => {
              const ans = a.answers?.find((r) => r.key === f.key);
              return (
                <td key={f.key} className="px-2 py-2">
                  {ans ? <AnswerCell r={ans} /> : <span className="text-xs text-muted">—</span>}
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
    </div>
  );
}
