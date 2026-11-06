"use client";

import { ReactNode, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { DocumentUpload } from "@/components/DocumentUpload";
import { ActiveJobsBanner } from "@/components/ActiveJobsBanner";
import { ActivityPanel } from "@/components/ActivityPanel";
import {
  getProject,
  getOverview,
  listJobs,
  keepProject,
  listModels,
  Project,
  ProjectOverview,
  SearchInfo,
  ImportJob,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { isProjectLead } from "@/lib/role";
import { isUntouched, withSearch } from "@/lib/search";
import { RequireAuth } from "@/components/RequireAuth";
import { ModelRef } from "@/components/ModelRef";
import { Badge } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Spinner, ErrorBanner } from "@/components/ui/Feedback";

const pct = (n: number, d: number): number => (d > 0 ? Math.round((n / d) * 100) : 0);

/** R6 (spec v4): the standard analyses in chain order — each node after its parent. */
function orderChain(searches: SearchInfo[]): SearchInfo[] {
  const byId = new Map(searches.map((s) => [s.id, s]));
  const visited = new Set<string>();
  const out: SearchInfo[] = [];
  const visit = (s: SearchInfo) => {
    if (visited.has(s.id)) return;
    visited.add(s.id);
    const p = s.parent ? byId.get(s.parent.id) : undefined;
    if (p) visit(p);
    out.push(s);
  };
  const roots = searches.filter((s) => !s.parent || !byId.has(s.parent.id));
  const rest = searches.filter((s) => s.parent && byId.has(s.parent.id));
  roots.forEach(visit);
  rest.forEach(visit);
  return out;
}

/** R6 (spec v4): depth of each standard analysis in its chain (root = 0). */
function chainDepths(ordered: SearchInfo[]): Map<string, number> {
  const depth = new Map<string, number>();
  for (const s of ordered) {
    depth.set(s.id, s.parent && depth.has(s.parent.id) ? depth.get(s.parent.id)! + 1 : 0);
  }
  return depth;
}

function Kpi({ label, value, sub, title }: { label: string; value: ReactNode; sub?: ReactNode; title?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2.5" title={title}>
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 text-xl font-semibold tabular-nums text-foreground">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

/** Split bar: accepted / to re-review / set aside / not yet analyzed, over the search's batch. */
function LotBar({ s }: { s: SearchInfo }) {
  const t = useTranslations("project");
  const b = s.breakdown;
  const lot = s.lot_size || 1;
  const parts = [
    { key: "acc", n: b.AUTO_ACCEPTED ?? 0, cls: "bg-success", label: t("legendAccepted") },
    { key: "rev", n: b.NEEDS_HUMAN_REVIEW ?? 0, cls: "bg-warning", label: t("legendReview") },
    { key: "rej", n: b.AUTO_REJECTED ?? 0, cls: "bg-danger", label: t("legendRejected") },
    { key: "err", n: b.ERROR ?? 0, cls: "bg-danger/50", label: t("legendError") },
  ];
  // The unanalyzed share is the bar background: name it in the legend only when non-zero.
  const rest = lot - parts.reduce((a, p) => a + p.n, 0);
  if (rest > 0) parts.push({ key: "na", n: rest, cls: "bg-surface-muted", label: t("legendUnanalysed") });
  return (
    <div className="flex flex-col gap-1">
      <div
        className="flex h-2 w-full overflow-hidden rounded-full bg-surface-muted"
        role="img"
        aria-label={parts.map((p) => `${p.label} ${p.n}`).join(", ")}
      >
        {parts.map((p) => (
          <div key={p.key} className={`h-full ${p.cls}`} style={{ width: `${(p.n / lot) * 100}%` }} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
        {parts.map((p) => (
          <span key={p.key}>
            <span className={`mr-1 inline-block h-2 w-2 rounded-full align-middle ${p.cls}`} />
            {p.n.toLocaleString()} {p.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function SearchRow({ s, projectId, isLead, hasCorpus }: { s: SearchInfo; projectId: string; isLead: boolean; hasCorpus: boolean }) {
  const t = useTranslations("project");
  const ts = useTranslations("searches");
  const tj = useTranslations("jobs");
  const open = (page: string) => withSearch(`/projects/${projectId}/${page}`, s.id);
  const blind = s.kind === "blind";
  const untouched = isUntouched(s);
  const analysedPct = pct(s.predicted, s.lot_size);
  const reviewedPct = pct(s.reviewed, s.lot_size);
  const running = s.last_job?.status === "RUNNING" || s.last_job?.status === "PENDING";
  return (
    <li className="rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold text-foreground">{s.name}</h3>
            {untouched && <Badge color="blue">{ts("badgeToLaunch")}</Badge>}
            {s.is_default && <Badge color="blue">{t("badgeInitial")}</Badge>}
            {s.kind !== "standard" && <Badge color="gray">{t(`kind_${s.kind}`)}</Badge>}
            {running && <Badge color="yellow">{t("running")}</Badge>}
            {s.schedule_state === "waiting" && <Badge color="gray">{t("scheduled")}</Badge>}
            {s.schedule_state === "failed" && <Badge color="red">{t("scheduleFailed")}</Badge>}
          </div>
          {s.question ? (
            <p className="mt-0.5 line-clamp-2 text-sm text-muted" title={s.question}>
              {s.question}
            </p>
          ) : s.last_benchmark ? (
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-muted">
              {ts("noQuestion")}
              <Link
                href={`/projects/${projectId}/benchmarks?run=${s.last_benchmark.id}`}
                className="font-medium text-primary underline-offset-2 hover:underline"
              >
                ⇢ {t("benchmarkOnLot", { status: tj(`status_${s.last_benchmark.status}`) })}
              </Link>
            </p>
          ) : (
            <p className="mt-0.5 line-clamp-2 text-sm text-muted">{ts("noQuestion")}</p>
          )}
          {s.schedule_state === "waiting" && s.parent && (
            <p className="mt-0.5 text-xs text-muted">{t("scheduledAfter", { name: s.parent.name })}</p>
          )}
          {s.schedule_state === "failed" && (
            <p className="mt-0.5 text-xs text-danger">{t(`scheduleError_${s.schedule?.error ?? "unknown"}`)}</p>
          )}
          <p className="mt-0.5 text-xs text-muted">
            {t("lotSize", { n: s.lot_size })}
            {s.parent && <> · {t("derivedFrom", { name: s.parent.name })}</>}
            {s.last_job && <> · <ModelRef name={s.last_job.model} version={s.last_job.model_version} /></>}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {untouched ? (
            // never launched: one action — select the analysis and open the pre-filled panel
            <Link href={`/projects/${projectId}/inference?search=${s.id}&new=1`}>
              <Button size="sm">{ts("launch")} →</Button>
            </Link>
          ) : (
            <>
              <Link href={open("articles")}>
                <Button size="sm" variant="secondary">
                  {t("openResults")}
                </Button>
              </Link>
              {s.to_review > 0 && (
                <Link href={open("annotate?queue=review")}>
                  <Button size="sm">{t("reviewN", { n: s.to_review })}</Button>
                </Link>
              )}
              {isLead && hasCorpus && !running && s.schedule_state !== "waiting" && (
                <Link href={open("inference")}>
                  <Button size="sm" variant="ghost">
                    {s.predicted > 0 ? t("rerun") : t("analyse")}
                  </Button>
                </Link>
              )}
            </>
          )}
        </div>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
        {blind ? (
          <p className="text-xs text-muted">{t("blindHint", { done: s.reviewed, total: s.lot_size })}</p>
        ) : s.predicted > 0 ? (
          <LotBar s={s} />
        ) : (
          <p className="text-xs text-muted">{t("notAnalysedYet")}</p>
        )}
        <div className="flex gap-4 text-sm tabular-nums">
          {!blind && (
            <span>
              <strong className="text-foreground">{analysedPct} %</strong>{" "}
              <span className="text-xs text-muted">{t("analysedShort")} {t("ofLot", { n: s.predicted, d: s.lot_size })}</span>
            </span>
          )}
          <span>
            <strong className="text-foreground">{reviewedPct} %</strong>{" "}
            <span className="text-xs text-muted">{t("reviewedShort")} {t("ofLot", { n: s.reviewed, d: s.lot_size })}</span>
          </span>
        </div>
      </div>
    </li>
  );
}

function ProjectDashboard() {
  const t = useTranslations("project");
  const tAct = useTranslations("activity");
  const projectId = useParams<{ id: string }>().id;
  const { user } = useAuth();
  const locale = useLocale();

  const [project, setProject] = useState<Project | null>(null);
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [jobs, setJobs] = useState<ImportJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modelCount, setModelCount] = useState<number | null>(null);

  const load = useCallback(() => {
    return Promise.all([getProject(projectId), getOverview(projectId), listJobs(projectId)])
      .then(([p, o, js]) => {
        setProject(p);
        setOverview(o);
        setJobs(js);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "error"));
  }, [projectId]);


  useEffect(() => {
    load();
    listModels(projectId)
      .then((m) => setModelCount(m.length))
      .catch(() => setModelCount(null));
  }, [load, projectId]);

  if (error) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!project || !overview) return <Spinner label={t("loading")} />;

  const isLead = isProjectLead(project, user);
  const hasCorpus = overview.articles > 0;
  const toReviewTotal = overview.searches.reduce((a, s) => a + s.to_review, 0);
  const imports = (jobs ?? []).filter((j) => j.kind !== "fulltext");
  const lastJob = imports.length > 0 ? imports[imports.length - 1] : null;
  // launched + to-launch: the never-launched initial analyses are shown with their own badge
  const shown = overview.searches;

  // spec v4 R6: standard analyses form the chain; the rest stays outside it.
  const standard = shown.filter((s) => s.kind === "standard");
  const outside = shown.filter((s) => s.kind !== "standard");
  const chain = orderChain(standard);
  const depths = chainDepths(chain);
  const benchCount = shown.filter((s) => s.last_benchmark).length;

  return (
    <div className="flex flex-col gap-5">
      <ActiveJobsBanner projectId={projectId} />
      {project.retention_delete_on && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger/40 bg-danger/10 p-3 text-sm">
          <span className="text-danger">
            {t("retentionWarn", { date: new Date(project.retention_delete_on).toLocaleDateString(locale) })}
          </span>
          {isLead && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => void keepProject(projectId).then(() => load()).catch(() => undefined)}
            >
              {t("keepProject")}
            </Button>
          )}
        </div>
      )}

      {modelCount === 0 && (
        <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
          {t("noModels")}{" "}
          {user?.role === "ADMIN" ? (
            <Link href="/models" className="font-medium text-primary hover:underline">
              {t("addModels")} →
            </Link>
          ) : (
            <span className="text-muted">{t("askAdmin")}</span>
          )}
        </div>
      )}

      {/* Global counts: distinct articles, across all searches */}
      <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Kpi label={t("kpiArticles")} value={overview.articles.toLocaleString()} />
        <Kpi
          label={t("kpiFulltext")}
          value={`${pct(overview.fulltext, overview.articles)} %`}
          sub={t("outOf", { n: overview.fulltext })}
        />
        <Kpi
          label={t("kpiAnalysed")}
          value={`${pct(overview.analysed, overview.articles)} %`}
          sub={t("outOf", { n: overview.analysed })}
          title={t("kpiScopeAll")}
        />
        <Kpi
          label={t("kpiReviewed")}
          value={`${pct(overview.reviewed, overview.articles)} %`}
          sub={t("outOf", { n: overview.reviewed })}
          title={t("kpiScopeAll")}
        />
        <Kpi label={t("kpiToReview")} value={toReviewTotal.toLocaleString()} title={t("kpiScopeAll")} />
        <Kpi label={t("kpiGold")} value={overview.gold.toLocaleString()} />
      </section>

      {/* The searches: open directly the results, the re-review or the analysis of each */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-base font-semibold text-foreground">
            {t("searchesTitle")} <span className="text-sm font-normal text-muted">{shown.length}</span>
          </h2>
        </div>
        {overview.articles === 0 ? (
          <div className="flex flex-col gap-3">
            <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted">
              {t("emptyCorpus")}{" "}
              {isLead && (
                <Link href={`/projects/${projectId}/import`} className="font-medium text-primary hover:underline">
                  {t("ctaImport")} →
                </Link>
              )}
            </p>
            {isLead && <DocumentUpload projectId={projectId} />}
          </div>
        ) : shown.length === 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-primary/40 bg-primary/5 p-4 text-sm text-foreground">
            <span>{t("noSearchYet", { n: overview.articles })}</span>
            {isLead && (
              <Link href={`/projects/${projectId}/inference?new=1`}>
                <Button>{t("runAnalysis")} →</Button>
              </Link>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {chain.length > 0 && (
              // The chain of standard analyses: each node after its parent, with its origin
              <ol className="flex flex-col gap-3" aria-label={t("chainAria")}>
                {chain.map((s, i) => {
                  const depth = depths.get(s.id) ?? 0;
                  const parent = s.parent && chain[i - 1]?.id === s.parent.id ? s.parent : undefined;
                  return (
                    <li key={s.id} className="flex flex-col" style={{ marginLeft: `${depth * 1.25}rem` }}>
                      {parent && (
                        <p className="mb-1 pl-1 text-xs text-muted">
                          <span aria-hidden>↓ </span>
                          {s.parent_spec?.status === "NEEDS_HUMAN_REVIEW"
                            ? t("derivedFromUncertains", { n: s.lot_size, name: parent.name })
                            : t("derivedFrom", { name: parent.name })}
                        </p>
                      )}
                      <SearchRow s={s} projectId={projectId} isLead={isLead} hasCorpus={hasCorpus} />
                    </li>
                  );
                })}
              </ol>
            )}
            {outside.length > 0 && (
              <div className="flex flex-col gap-2">
                <h3 className="text-sm font-medium text-muted">{t("outsideChain")}</h3>
                <ul className="flex flex-col gap-3">
                  {outside.map((s) => (
                    <SearchRow key={s.id} s={s} projectId={projectId} isLead={isLead} hasCorpus={hasCorpus} />
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>

      {/* spec v4 R6: gold & benchmarks — optional tooling, outside the chain */}
      {overview.articles > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-base font-semibold text-foreground">{t("optionalTooling")}</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            <Link
              href={`/projects/${projectId}/gold`}
              className="flex items-center justify-between rounded-lg border border-border bg-surface p-3 transition-colors hover:border-primary"
            >
              <span className="font-medium text-foreground">{t("goldCard")}</span>
              <span className="text-sm text-muted">{t("goldCardState", { n: overview.gold })}</span>
              <span aria-hidden>→</span>
            </Link>
            <Link
              href={`/projects/${projectId}/benchmarks`}
              className="flex items-center justify-between rounded-lg border border-border bg-surface p-3 transition-colors hover:border-primary"
            >
              <span className="font-medium text-foreground">{t("benchCard")}</span>
              <span className="text-sm text-muted">{t("benchCardState", { n: benchCount })}</span>
              <span aria-hidden>→</span>
            </Link>
          </div>
        </section>
      )}

      {lastJob && (
        <p className="text-xs text-muted">
          {t("lastImport")} :{" "}
          <code className="inline-block max-w-full truncate align-bottom text-foreground" title={lastJob.query ?? undefined}>
            {lastJob.query}
          </code>
          {lastJob.result && (
            <>
              {" "}
              · {t("imported")} : {String(lastJob.result.imported ?? 0)} · {t("failed")} : {String(lastJob.result.failed ?? 0)}
            </>
          )}
          {lastJob.created_at && <> · {new Date(lastJob.created_at).toLocaleString()}</>}
        </p>
      )}

      <details className="rounded-lg border border-border bg-surface p-3 text-sm">
        <summary className="cursor-pointer font-medium text-foreground">{tAct("title")}</summary>
        <div className="mt-2">
          <ActivityPanel projectId={projectId} />
        </div>
      </details>

      <details className="rounded-lg border border-border bg-surface p-3 text-sm">
        <summary className="cursor-pointer font-medium text-foreground">{t("howTitle")}</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-muted">
          <li>{t("howStep1")}</li>
          <li>{t("howStep2")}</li>
          <li>{t("howStep3")}</li>
        </ol>
      </details>
    </div>
  );
}

export default function ProjectPage() {
  return (
    <RequireAuth>
      <ProjectDashboard />
    </RequireAuth>
  );
}
