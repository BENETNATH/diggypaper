"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { ActiveProjectJob, getActiveProjectJobs } from "@/lib/api";
import { usePolling } from "@/components/ui/usePolling";

const POLL_MS = 5000;

/**
 * §1 — The project's in-progress jobs (imports / analyses / benchmarks), at the
 * top of the project page. Re-fetched every 5 s while a job is running;
 * hidden when nothing is active.
 */
export function ActiveJobsBanner({ projectId }: { projectId: string }) {
  const t = useTranslations("activity");
  const { data } = usePolling(
    () => getActiveProjectJobs(projectId),
    POLL_MS,
    (d) => d.jobs.length === 0
  );
  const jobs = data?.jobs;
  if (!jobs || jobs.length === 0) return null;

  return (
    <section
      data-testid="active-jobs"
      aria-label={t("activeJobs")}
      className="rounded-lg border border-primary/40 bg-primary/5 p-3"
    >
      <ActiveJobsList jobs={jobs} projectId={projectId} />
    </section>
  );
}

function ActiveJobsList({ jobs, projectId }: { jobs: ActiveProjectJob[]; projectId: string }) {
  const t = useTranslations("activity");
  const tj = useTranslations("jobs");
  const locale = useLocale();
  return (
    <>
      <h2 className="text-sm font-semibold text-foreground">{t("activeJobs")}</h2>
      <ul className="mt-2 flex flex-col gap-1.5">
        {jobs.map((j) => (
          <li key={j.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm text-foreground">
            <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-primary" aria-hidden />
            <span className="font-medium">{t(j.kind)}</span>
            {j.detail && (
              <code className="max-w-full truncate text-xs text-muted" title={j.detail}>
                {j.detail}
              </code>
            )}
            {j.progress && j.progress.total > 0 && (
              <span className="text-xs tabular-nums text-muted">
                {j.progress.done}/{j.progress.total}
              </span>
            )}
            {j.models && j.models.length > 0 && (
              <span className="text-xs text-muted">{j.models.join(", ")}</span>
            )}
            <span className="text-xs text-muted">
              · {tj(`status_${j.status}`)}
              {j.started_at && <> · {new Date(j.started_at).toLocaleString(locale)}</>}
            </span>
            {j.kind === "analysis" && (
              <Link
                href={`/projects/${projectId}/inference${j.search_id ? `?search=${j.search_id}` : ""}`}
                className="text-xs text-primary hover:underline"
              >
                {t("open")}
              </Link>
            )}
            {j.kind === "benchmark" && (
              <Link
                href={`/projects/${projectId}/benchmarks`}
                className="text-xs text-primary hover:underline"
              >
                {t("open")}
              </Link>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}
