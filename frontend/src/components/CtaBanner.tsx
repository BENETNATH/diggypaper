"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { SearchInfo } from "@/lib/api";
import { withSearch } from "@/lib/search";
import { Button } from "@/components/ui/Button";

/**
 * CTA banner at the end of an analysis (spec 2026-11-09-sous-analyse-criteres
 * R3): the natural next steps under the stats, when the job is COMPLETED.
 * One primary button only — when a derived search B already exists,
 * « Voir « B » » takes the primary slot and « Nouvelle analyse » steps down.
 * (The mockup's 5th button — re-run the errors — was dropped: no existing
 * re-run endpoint is scoped to the ERROR articles.)
 */
export function CtaBanner({
  projectId,
  searchId,
  nResults,
  nReview,
  derived,
  onNewAnalysis,
  onChat,
  children,
}: {
  projectId: string;
  /** The completed analysis (A). */
  searchId: string;
  /** Number of results of the run (processed articles). */
  nResults: number;
  /** Uncertain articles (NEEDS_HUMAN_REVIEW, not yet reviewed). */
  nReview: number;
  /** First search already derived from A (its lot is snapshotted). */
  derived?: SearchInfo | null;
  /** Button 1 (no derived search yet): open the creation form with A preselected. */
  onNewAnalysis: () => void;
  /** Button 3: switch the chat panel to the results mode of A. */
  onChat: () => void;
  /** Extra items (the export menu). */
  children?: ReactNode;
}) {
  const t = useTranslations("inference");
  return (
    <div data-testid="cta-banner" className="mt-4 rounded-lg bg-surface shadow-sm p-3">
      <p className="mb-3 text-sm font-semibold text-foreground">
        <span className="mr-1 text-success">✓</span>
        {t("ctaDone", { n: nResults })}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {derived && (
          <Link href={withSearch(`/projects/${projectId}/articles`, derived.id)}>
            <Button size="sm">{t("ctaSeeDerived", { name: derived.name, n: derived.lot_size })}</Button>
          </Link>
        )}
        <Button size="sm" variant={derived ? "secondary" : undefined} onClick={onNewAnalysis}>
          {t("ctaNewAnalysis", { n: nResults })}
        </Button>
        {nReview > 0 && (
          <Link href={withSearch(`/projects/${projectId}/annotate?queue=review`, searchId)}>
            <Button size="sm" variant="secondary">
              {t("ctaReview", { n: nReview })}
            </Button>
          </Link>
        )}
        <Button size="sm" variant="secondary" onClick={onChat}>
          {t("ctaChat")}
        </Button>
        <Link href={withSearch(`/projects/${projectId}/articles`, searchId)}>
          <Button size="sm" variant="secondary">
            {t("ctaSeeAll")}
          </Button>
        </Link>
        {children}
      </div>
    </div>
  );
}
