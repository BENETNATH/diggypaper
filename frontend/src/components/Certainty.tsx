"use client";

import { useTranslations } from "next-intl";

/** Certainty (0–1) of ONE model answer: colored pill + percentage. */
export function Certainty({ value, className = "" }: { value: number | null | undefined; className?: string }) {
  const t = useTranslations("articles");
  if (value == null) return null;
  const pct = Math.round(value * 100);
  const tone = pct >= 85 ? "bg-success/15 text-success" : pct >= 60 ? "bg-warning/15 text-warning" : "bg-danger/15 text-danger";
  return (
    <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${tone} ${className}`} title={t("certaintyHelp")}>
      {pct} %
    </span>
  );
}

/** "Author ... Last author · Journal · Date" row of an article. */
export function biblioLine(a: {
  first_author?: string | null;
  last_author?: string | null;
  journal?: string | null;
  pub_date?: string | null;
}): string {
  const authors = a.first_author ? (a.last_author ? `${a.first_author} … ${a.last_author}` : a.first_author) : "";
  return [authors, a.journal, a.pub_date].filter(Boolean).join(" · ");
}

/** External links of an article: DOI and full text / PDF (when they exist). */
export function ArticleLinks({
  article,
  className = "",
}: {
  article: { doi_url?: string | null; fulltext_url?: string | null };
  className?: string;
}) {
  const t = useTranslations("articles");
  if (!article.doi_url && !article.fulltext_url) return null;
  const cls = "text-primary hover:underline";
  return (
    <span className={`flex flex-wrap gap-x-3 text-xs ${className}`}>
      {article.doi_url && (
        <a href={article.doi_url} target="_blank" rel="noopener noreferrer" className={cls}>
          DOI ↗
        </a>
      )}
      {article.fulltext_url && (
        <a href={article.fulltext_url} target="_blank" rel="noopener noreferrer" className={cls}>
          {t("openFulltext")} ↗
        </a>
      )}
    </span>
  );
}
