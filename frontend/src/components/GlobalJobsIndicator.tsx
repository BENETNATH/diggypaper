"use client";

import { useTranslations } from "next-intl";
import { getGlobalActiveJobs, GlobalActiveJobs } from "@/lib/api";
import { usePolling } from "@/components/ui/usePolling";

const POLL_MS = 30000;

/**
 * §2 — The instance-wide « jobs in progress » indicator (top of the projects
 * list). Counters only — no project information — refreshed every 30 s while
 * the page is open, hidden when count == 0.
 */
export function GlobalJobsIndicator() {
  const t = useTranslations("activity");
  const { data } = usePolling<GlobalActiveJobs>(getGlobalActiveJobs, POLL_MS, () => false);
  if (!data || data.count === 0) return null;

  const parts = (
    [
      ["import", data.by_kind.import],
      ["analysis", data.by_kind.analysis],
      ["benchmark", data.by_kind.benchmark],
    ] as const
  )
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${t(k)} : ${n}`);

  return (
    <p
      data-testid="global-jobs"
      title={parts.join(" · ")}
      className="flex items-center gap-2 text-sm text-primary"
    >
      <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 tabular-nums">
        <span className="relative flex h-2 w-2" aria-hidden>
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
        </span>
        {data.count}
      </span>
      {t("globalJobsOnInstance")}
    </p>
  );
}
