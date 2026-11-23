"use client";

import { useTranslations } from "next-intl";
import { getGlobalActiveJobs, GlobalActiveJobs } from "@/lib/api";
import { usePolling } from "@/components/ui/usePolling";

const POLL_MS = 30000;

/**
 * §2 — The instance-wide « jobs in progress » indicator (top of the projects
 * list). Counters only — no project information — refreshed every 30 s while
 * the page is open, hidden when count == 0. audit 2026-11-10 P3-4: it keeps polling
 * (cheap, 30s) so a new job started elsewhere reappears while the page is open — the
 * render is what stops, not the poll (an endless stop would never re-arm on this page).
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
      // audit 2026-11-10 P3-12: the per-kind breakdown is visible (it was title-only,
      // unreachable by keyboard and touch).
      className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-primary"
    >
      <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 tabular-nums">
        <span className="relative flex h-2 w-2" aria-hidden>
          <span className="absolute inline-flex h-full w-full animate-pulse rounded-full bg-primary opacity-60 motion-reduce:animate-none" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
        </span>
        {data.count}
      </span>
      {t("globalJobsOnInstance")}
      <span className="text-xs text-muted">{parts.join(" · ")}</span>
    </p>
  );
}
