"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/Feedback";

/**
 * S1 state of the corpus-centric pages (spec 2026-11-20-analyses-runs-2 R2):
 * the project has no corpus of questions yet. The hint names the waiting articles
 * only when the count is known (the overview fetch may still be in flight).
 */
export function CorporaEmpty({
  projectId,
  articleCount = null,
  isLead,
  /** Opens the creation panel inline (inference page); the other pages link to it. */
  onNew,
}: {
  projectId: string;
  articleCount?: number | null;
  isLead: boolean;
  onNew?: () => void;
}) {
  const ti = useTranslations("inference");
  const tp = useTranslations("project");
  if (!isLead) {
    return (
      <Card className="p-4">
        <EmptyState title={ti("corporaEmpty")} hint={articleCount != null ? ti("corporaEmptyHint", { n: articleCount }) : undefined} />
      </Card>
    );
  }
  const cta = (
    <Button onClick={onNew}>{tp("pillNew")}</Button>
  );
  return (
    <Card className="p-4">
      <EmptyState
        title={ti("corporaEmpty")}
        hint={articleCount != null ? ti("corporaEmptyHint", { n: articleCount }) : undefined}
        action={onNew ? cta : <Link href={`/projects/${projectId}/inference?new=1`}>{cta}</Link>}
      />
    </Card>
  );
}
