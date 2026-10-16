"use client";

import { ReactNode, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
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
import { Badge } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Spinner, ErrorBanner } from "@/components/ui/Feedback";

const pct = (n: number, d: number): number => (d > 0 ? Math.round((n / d) * 100) : 0);

function Kpi({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2.5">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 text-xl font-semibold tabular-nums text-foreground">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

/** Barre découpée : validés / à relire / écartés / pas encore analysés, sur le lot de la recherche. */
function LotBar({ s }: { s: SearchInfo }) {
  const t = useTranslations("project");
  const b = s.breakdown;
  const lot = s.lot_size || 1;
  const parts = [
    { key: "acc", n: b.AUTO_ACCEPTED ?? 0, cls: "bg-success", label: t("legendAccepted") },
    { key: "rev", n: b.NEEDS_HUMAN_REVIEW ?? 0, cls: "bg-warning", label: t("legendReview") },
    { key: "rej", n: b.AUTO_REJECTED ?? 0, cls: "bg-danger", label: t("legendRejected") },
  ];
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
  const open = (page: string) => withSearch(`/projects/${projectId}/${page}`, s.id);
  const blind = s.kind === "blind";
  const analysedPct = pct(s.predicted, s.lot_size);
  const reviewedPct = pct(s.reviewed, s.lot_size);
  const running = s.last_job?.status === "RUNNING" || s.last_job?.status === "PENDING";
  return (
    <li className="rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold text-foreground">{s.name}</h3>
            {s.is_default && <Badge color="blue">{t("badgeInitial")}</Badge>}
            {s.kind !== "standard" && <Badge color="gray">{t(`kind_${s.kind}`)}</Badge>}
            {running && <Badge color="yellow">{t("running")}</Badge>}
            {s.schedule_state === "waiting" && <Badge color="gray">{t("scheduled")}</Badge>}
            {s.schedule_state === "failed" && <Badge color="red">{t("scheduleFailed")}</Badge>}
          </div>
          <p className="mt-0.5 line-clamp-1 text-sm text-muted" title={s.question}>
            {s.question}
          </p>
          {s.schedule_state === "waiting" && s.parent && (
            <p className="mt-0.5 text-xs text-muted">{t("scheduledAfter", { name: s.parent.name })}</p>
          )}
          {s.schedule_state === "failed" && (
            <p className="mt-0.5 text-xs text-danger">{t(`scheduleError_${s.schedule?.error ?? "unknown"}`)}</p>
          )}
          <p className="mt-0.5 text-xs text-muted">
            {t("lotSize", { n: s.lot_size })}
            {s.parent && <> · {t("derivedFrom", { name: s.parent.name })}</>}
            {s.last_job && <> · {s.last_job.model}</>}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
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
            <span title={t("analysedPctHelp")}>
              <strong className="text-foreground">{analysedPct} %</strong>{" "}
              <span className="text-xs text-muted">{t("analysedShort")}</span>
            </span>
          )}
          <span title={t("reviewedPctHelp")}>
            <strong className="text-foreground">{reviewedPct} %</strong>{" "}
            <span className="text-xs text-muted">{t("reviewedShort")}</span>
          </span>
        </div>
      </div>
    </li>
  );
}

function ProjectDashboard() {
  const t = useTranslations("project");
  const projectId = useParams<{ id: string }>().id;
  const { user } = useAuth();
  const locale = useLocale();

  const [project, setProject] = useState<Project | null>(null);
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [jobs, setJobs] = useState<ImportJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modelCount, setModelCount] = useState<number | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [p, o, js] = await Promise.all([getProject(projectId), getOverview(projectId), listJobs(projectId)]);
      setProject(p);
      setOverview(o);
      setJobs(js);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    listModels(projectId).then((m) => setModelCount(m.length)).catch(() => setModelCount(null));
  }, [load, projectId]);

  if (error) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!project || !overview) return <Spinner label={t("loading")} />;

  const isLead = isProjectLead(project, user);
  const hasCorpus = overview.articles > 0;
  const toReviewTotal = overview.searches.reduce((a, s) => a + s.to_review, 0);
  const imports = (jobs ?? []).filter((j) => j.kind !== "fulltext");
  const lastJob = imports.length > 0 ? imports[imports.length - 1] : null;
  const initial = overview.searches.find((s) => s.is_default);
  const shown = overview.searches.filter((s) => !isUntouched(s));

  return (
    <div className="flex flex-col gap-5">
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

      {/* Chiffres globaux : articles distincts, toutes recherches confondues */}
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
        />
        <Kpi
          label={t("kpiReviewed")}
          value={`${pct(overview.reviewed, overview.articles)} %`}
          sub={t("outOf", { n: overview.reviewed })}
        />
        <Kpi label={t("kpiToReview")} value={toReviewTotal.toLocaleString()} />
        <Kpi label={t("kpiGold")} value={overview.gold.toLocaleString()} />
      </section>

      {/* Les recherches : ouvrir directement les résultats, la relecture ou l'analyse de chacune */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-base font-semibold text-foreground">
            {t("searchesTitle")} <span className="text-sm font-normal text-muted">{shown.length}</span>
          </h2>
          {isLead && hasCorpus && (
            <Link href={withSearch(`/projects/${projectId}/searches`, initial?.id)}>
              <Button size="sm" variant="secondary">
                + {t("newSearch")}
              </Button>
            </Link>
          )}
        </div>
        {overview.articles === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted">
            {t("emptyCorpus")}{" "}
            {isLead && (
              <Link href={`/projects/${projectId}/import`} className="font-medium text-primary hover:underline">
                {t("ctaImport")} →
              </Link>
            )}
          </p>
        ) : shown.length === 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-primary/40 bg-primary/5 p-4 text-sm text-foreground">
            <span>{t("noSearchYet", { n: overview.articles })}</span>
            {isLead && (
              <Link href={`/projects/${projectId}/inference`}>
                <Button>{t("runAnalysis")} →</Button>
              </Link>
            )}
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {shown.map((s) => (
              <SearchRow key={s.id} s={s} projectId={projectId} isLead={isLead} hasCorpus={hasCorpus} />
            ))}
          </ul>
        )}
      </section>

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
        <summary className="cursor-pointer font-medium text-foreground">{t("howTitle")}</summary>
        <p className="mt-2 text-muted">{t("howBody")}</p>
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
