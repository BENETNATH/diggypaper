"use client";

import { useTranslations } from "next-intl";
import { VectorizeProgress } from "@/lib/api";
import { JobProgress } from "@/components/JobProgress";
import { Button } from "@/components/ui/Button";

/**
 * « Indexation visible »: the live state of the corpus index.
 *
 * - a run in flight → a live progress bar (n / N sections, reuses JobProgress);
 * - a corpus not yet indexed → the « not indexed » line + the index button
 *   (lead) + the transient « started » hint right after the trigger;
 * - otherwise → nothing (chat/semantic), or a subtle « indexed » note
 *   (``showIndexed``, e.g. the overview).
 *
 * Driven by the cheap status read from ``useVectorizeProgress`` — not by a
 * probe semantic search.
 */
export function IndexProgress({
  progress,
  isLead,
  indexing,
  onIndex,
  showIndexed = false,
  detailed = false,
}: {
  progress: VectorizeProgress | null;
  isLead: boolean;
  indexing: boolean;
  onIndex?: () => void;
  showIndexed?: boolean;
  detailed?: boolean;
}) {
  const t = useTranslations("index");

  if (progress === null) return null; // still loading the status
  if (progress.status === "RUNNING") {
    return (
      <JobProgress
        done={progress.done}
        total={progress.total}
        title={t("title")}
        inline={!detailed}
      />
    );
  }
  if (!progress.indexed) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
        <span data-testid="index-not-indexed">{t("notIndexed")}</span>
        {isLead && onIndex && (
          <Button
            variant="secondary"
            size="sm"
            data-testid="index-button"
            onClick={onIndex}
            disabled={indexing}
          >
            {t("indexButton")}
          </Button>
        )}
        {indexing && <span data-testid="index-started">{t("indexStarted")}</span>}
      </div>
    );
  }
  if (showIndexed) {
    return (
      <p data-testid="index-done" className="text-sm text-muted">
        {t("indexed")}
      </p>
    );
  }
  return null;
}
