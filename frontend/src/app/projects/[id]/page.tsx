"use client";

import { ReactNode, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { OnboardingSteps } from "@/components/OnboardingSteps";
import { orderChain, chainDepths } from "@/lib/chain";
import { ActiveJobsBanner } from "@/components/ActiveJobsBanner";
import { ActivityPanel } from "@/components/ActivityPanel";
import {
  getProject,
  getOverview,
  listJobs,
  keepProject,
  listModels,
  updateProject,
  Project,
  ProjectOverview,
  SearchInfo,
  ImportJob,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { isProjectLead } from "@/lib/role";
import { isUntouched, withSearch } from "@/lib/search";
import { useSearchId } from "@/lib/useSearch";
import { useVectorizeProgress } from "@/hooks/useVectorizeProgress";
import { IndexProgress } from "@/components/IndexProgress";
import { RequireAuth } from "@/components/RequireAuth";
import { ModelRef } from "@/components/ModelRef";
import { Badge } from "@/components/ui/Card";
import { TriageThresholds, type TriagePolicyValue } from "@/app/projects/[id]/inference/TriageThresholds";
import { Button } from "@/components/ui/Button";
import { Spinner, ErrorBanner } from "@/components/ui/Feedback";

const pct = (n: number, d: number): number => (d > 0 ? Math.round((n / d) * 100) : 0);

/**
 * spec clarte-1 R5: label + value + sub obligatoire. `highlight` = primary border
 * on the single actionable KPI (« À relire » > 0) — a pointer, not a state color.
 */
function Kpi({
  label,
  value,
  sub,
  highlight = false,
}: {
  label: string;
  value: ReactNode;
  sub: ReactNode;
  highlight?: boolean;
}) {
  return (
    <div className={`rounded-lg bg-surface shadow-sm px-3 py-2.5 ${highlight ? "border border-primary" : ""}`}>
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

function SearchRow({
  s,
  projectId,
  isLead,
  hasCorpus,
  active = false,
}: {
  s: SearchInfo;
  projectId: string;
  isLead: boolean;
  hasCorpus: boolean;
  active?: boolean;
}) {
  const t = useTranslations("project");
  const ts = useTranslations("searches");
  const tj = useTranslations("jobs");
  const open = (page: string) => withSearch(`/projects/${projectId}/${page}`, s.id);
  const blind = s.kind === "blind";
  const untouched = isUntouched(s);
  const analysedPct = pct(s.predicted, s.lot_size);
  const reviewedPct = pct(s.reviewed, s.lot_size);
  const running = s.last_job?.status === "RUNNING" || s.last_job?.status === "PENDING";
  const b = s.breakdown;
  return (
    <div
      className={`rounded-lg p-4 ${
        active
          ? "border-2 border-primary bg-primary/5"
          : untouched
            ? "border border-dashed border-border bg-surface"
            : "bg-surface shadow-sm"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold text-foreground">{s.name}</h3>
            {/* spec clarte-1 R2: one state = one badge; R3: the active card is pointed by
                its border only — no « active » badge, no « Initiale » badge */}
            {untouched && <Badge color="gray">{ts("badgeToLaunch")}</Badge>}
            {s.kind !== "standard" && <Badge color="gray">{t(`kind_${s.kind}`)}</Badge>}
            {running && <Badge color="yellow">{t("running")}</Badge>}
            {s.schedule_state === "waiting" && <Badge color="gray">{t("scheduled")}</Badge>}
            {s.schedule_state === "failed" && <Badge color="red">{t("scheduleFailed")}</Badge>}
          </div>
          {/* spec clarte-1 R2: before the first run the useful info is the lot,
              not « aucune question posée » (orientation is optional) */}
          {!untouched && (s.question ? (
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
          ))}
          {s.kind !== "standard" && (
            <p className="mt-1 text-xs text-muted">{ts(`kindHint_${s.kind}`)}</p>
          )}
          {s.schedule_state === "waiting" && s.parent && (
            <p className="mt-0.5 text-xs text-muted">{t("scheduledAfter", { name: s.parent.name })}</p>
          )}
          {s.schedule_state === "failed" && (
            <p className="mt-0.5 text-xs text-danger">{t(`scheduleError_${s.schedule?.error ?? "unknown"}`)}</p>
          )}
          <p className="mt-0.5 text-xs text-muted">
            {t("lotSize", { n: s.lot_size })}
            {s.predicted > 0 && (
              <>
                {" "}
                · <span className="text-success">{b.AUTO_ACCEPTED ?? 0} ✓</span> ·{" "}
                <span className="text-warning">{b.NEEDS_HUMAN_REVIEW ?? 0} ?</span> ·{" "}
                <span className="text-danger">{b.AUTO_REJECTED ?? 0} ✗</span>
              </>
            )}
            {s.parent && <> · {t("derivedFrom", { name: s.parent.name })}</>}
            {s.last_job && <> · <ModelRef name={s.last_job.model} version={s.last_job.model_version} /></>}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {untouched ? (
            // never launched: one state, one action (spec clarte-1 R2)
            <Link href={`/projects/${projectId}/inference?search=${s.id}&new=1`}>
              <Button size="sm">{ts("launch")}</Button>
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
        {/* spec clarte-1 R2: the 0 % lines are masked until the first run —
            the lot is the useful info before launching */}
        {blind ? (
          <p className="text-xs text-muted">{t("blindHint", { done: s.reviewed, total: s.lot_size })}</p>
        ) : s.predicted > 0 ? (
          <LotBar s={s} />
        ) : (
          <p className="text-xs text-muted">{t("toLaunchLotLine", { n: s.lot_size })}</p>
        )}
        <div className="flex gap-4 text-sm tabular-nums">
          {!blind && s.predicted > 0 && (
            <span>
              <strong className="text-foreground">{analysedPct} %</strong>{" "}
              <span className="text-xs text-muted">{t("analysedShort")} {t("ofLot", { n: s.predicted, d: s.lot_size })}</span>
            </span>
          )}
          {!blind && s.predicted > 0 && (
            <span>
              <strong className="text-foreground">{reviewedPct} %</strong>{" "}
              <span className="text-xs text-muted">{t("reviewedShort")} {t("ofLot", { n: s.reviewed, d: s.lot_size })}</span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function ProjectDashboard() {
  const t = useTranslations("project");
  const tAct = useTranslations("activity");
  const tInf = useTranslations("inference");
  const projectId = useParams<{ id: string }>().id;
  const { user } = useAuth();
  const locale = useLocale();

  const [project, setProject] = useState<Project | null>(null);
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [jobs, setJobs] = useState<ImportJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modelCount, setModelCount] = useState<number | null>(null);
  // spec clarte-3 R1: triage thresholds live on the overview (lead only); no policy = defaults 0.85/0.15.
  const [triage, setTriage] = useState<TriagePolicyValue>({ accept: 0.85, reject: 0.15 });

  // Live corpus-index state + trigger (cheap status read, not a probe) — the
  // overview is where the index progress is most visible (« indexation visible »).
  const { progress, indexing, index } = useVectorizeProgress(projectId);

  const load = useCallback(() => {
    return Promise.all([getProject(projectId), getOverview(projectId), listJobs(projectId)])
      .then(([p, o, js]) => {
        setProject(p);
        setOverview(o);
        setJobs(js);
        setTriage({ accept: p.triage_policy?.accept ?? 0.85, reject: p.triage_policy?.reject ?? 0.15 });
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

  // Optimistic patch of the triage thresholds (project-level); rolled back on failure
  // with a visible error (audit I-1: the old Analyse page surfaced the failure).
  const patchTriage = (next: TriagePolicyValue) => {
    const prev = triage;
    setTriage(next);
    updateProject(projectId, { triage_policy: next }).catch(() => {
      setTriage(prev);
      setError(tInf("triageSaveFailed"));
    });
  };

  const searchId = useSearchId(projectId);

  if (error) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!project || !overview) return <Spinner label={t("loading")} />;

  const isLead = isProjectLead(project, user);
  const hasCorpus = overview.articles > 0;
  const empty = overview.articles === 0;
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

      {!empty && modelCount === 0 && (
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

      {/* Global counts: distinct articles, across all searches.
          spec clarte-1 R5: the aggregate scope is a permanent micro-line (no tooltip),
          « — » before the first run, « À relire » highlighted only when actionable */}
      {!empty && (
        <section className="flex flex-col gap-1.5">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Kpi label={t("kpiArticles")} value={overview.articles.toLocaleString()} sub={t("outOf", { n: overview.articles })} />
            <Kpi
              label={t("kpiFulltext")}
              value={`${pct(overview.fulltext, overview.articles)} %`}
              sub={t("outOf", { n: overview.fulltext })}
            />
            <Kpi
              label={t("kpiAnalysed")}
              value={overview.analysed === 0 ? "—" : `${pct(overview.analysed, overview.articles)} %`}
              sub={overview.analysed === 0 ? null : t("outOf", { n: overview.analysed })}
            />
            <Kpi
              label={t("kpiReviewed")}
              value={overview.reviewed === 0 ? "—" : `${pct(overview.reviewed, overview.articles)} %`}
              sub={overview.reviewed === 0 ? null : t("outOf", { n: overview.reviewed })}
            />
            <Kpi
              label={t("kpiToReview")}
              value={toReviewTotal.toLocaleString()}
              sub={toReviewTotal > 0 ? t("outOf", { n: toReviewTotal }) : t("kpiToReviewZero")}
              highlight={toReviewTotal > 0}
            />
            <Kpi label={t("kpiGold")} value={overview.gold.toLocaleString()} sub={t("kpiGoldSub")} />
          </div>
          <p className="text-xs text-muted">{t("kpiScopeLine")}</p>
        </section>
      )}

      {/* spec clarte-3 R1: triage thresholds — project settings, lead only (moved out of Analyse). */}
      {isLead && !empty && (
        <section className="flex flex-col gap-2 rounded-lg bg-surface shadow-sm p-3">
          <h2 className="text-base font-semibold text-foreground">{t("settingsTitle")}</h2>
          <p className="text-xs text-muted">{tInf("thresholdsHint")}</p>
          <TriageThresholds value={triage} onChange={patchTriage} />
        </section>
      )}

      {/* Corpus index status + live progress (« indexation visible ») */}
      {!empty && progress !== null && (
        <section className="rounded-lg bg-surface shadow-sm p-3">
          <IndexProgress
            progress={progress}
            isLead={isLead}
            indexing={indexing}
            onIndex={() => void index()}
            showIndexed
            detailed
          />
        </section>
      )}

      {/* The searches: open directly the results, the re-review or the analysis of each */}
      {!empty && (
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-base font-semibold text-foreground">
              {chain.length > 0 ? t("chainTitle") : t("searchesTitle")}{" "}
              <span className="text-sm font-normal text-muted">
                {chain.length > 0 ? t("chainCount", { n: chain.length }) : shown.length}
              </span>
            </h2>
          </div>
          {shown.length === 0 ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-primary/5 p-4 text-sm text-foreground">
              <span>{t("noSearchYet", { n: overview.articles })}</span>
              {isLead && (
                <Link href={`/projects/${projectId}/inference?new=1`}>
                  <Button>{t("runAnalysis")}</Button>
                </Link>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {chain.length > 0 && (
                // The chain of standard analyses: each node after its parent, lettered A, B, C…
                <ol className="flex flex-col gap-4" aria-label={t("chainAria")}>
                  {chain.map((s, i) => {
                    const letter = String.fromCharCode(65 + Math.min(i, 25));
                    const depth = depths.get(s.id) ?? 0;
                    const parent = s.parent && chain[i - 1]?.id === s.parent.id ? s.parent : undefined;
                    const active = s.id === searchId;
                    return (
                      <li key={s.id} className="flex gap-3" style={{ marginLeft: `${depth * 1.5}rem` }}>
                        <div className="flex flex-col items-center" aria-hidden>
                          {/* spec clarte-1 R3: green = results (predicted > 0 || reviewed > 0,
                              same state as the pill dot), neutral = not started,
                              primary ring = selection (pointer, not a state) */}
                          <span
                            className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                              active
                                ? "bg-surface text-primary ring-2 ring-primary"
                                : s.predicted > 0 || s.reviewed > 0
                                  ? "bg-success text-white"
                                  : "bg-muted text-white"
                            }`}
                          >
                            {letter}
                          </span>
                          {i < chain.length - 1 && <span className="w-0.5 flex-1 rounded bg-border" />}
                        </div>
                        <div className="min-w-0 flex-1">
                          {parent && (
                            <p className="mb-1 pl-1 text-xs text-muted">
                              <span aria-hidden>↳ </span>
                              {s.parent_spec?.status === "NEEDS_HUMAN_REVIEW"
                                ? `${t("derivedFromUncertains", { n: s.lot_size, name: parent.name })} ↓`
                                : `${t("derivedFrom", { name: parent.name })} ↓`}
                            </p>
                          )}
                          <SearchRow
                            s={s}
                            projectId={projectId}
                            isLead={isLead}
                            hasCorpus={hasCorpus}
                            active={active}
                          />
                        </div>
                      </li>
                    );
                  })}
                </ol>
              )}
              {outside.length > 0 && (
                <div className="flex flex-col gap-2">
                  <div>
                    <h3 className="text-sm font-medium text-muted">{t("outsideChain")}</h3>
                    <p className="text-xs text-muted">{t("outsideChainHint")}</p>
                  </div>
                  <div className="flex flex-col gap-3">
                    {outside.map((s) => (
                      <SearchRow key={s.id} s={s} projectId={projectId} isLead={isLead} hasCorpus={hasCorpus} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      )}

      {/* spec v4: empty project → the 4-step onboarding (KPIs, analyses and accordions stay hidden) */}
      {empty && <OnboardingSteps projectId={projectId} modelCount={modelCount} isLead={isLead} />}

      {/* spec clarte-3 R5: optional tooling — a compact reminder, no cards
          (surfaces policy: semantic tokens only, bg-surface-muted, no border, no violet). */}
      {overview.articles > 0 && (
        <section className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-surface-muted p-3">
          <h2 className="text-sm font-semibold text-foreground">{t("optionalToolingIntro")}</h2>
          <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            <Link href={`/projects/${projectId}/gold`} className="text-primary hover:underline">
              {t("openGold", { n: overview.gold })}
            </Link>
            <Link href={`/projects/${projectId}/benchmarks`} className="text-primary hover:underline">
              {t("openBench", { n: benchCount })}
            </Link>
          </span>
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

      {!empty && (
        <details className="rounded-lg bg-surface shadow-sm p-3 text-sm">
          <summary className="cursor-pointer font-medium text-foreground">{tAct("title")}</summary>
          <div className="mt-2">
            <ActivityPanel projectId={projectId} />
          </div>
        </details>
      )}

      {!empty && (
        <details className="rounded-lg bg-surface shadow-sm p-3 text-sm">
          <summary className="cursor-pointer font-medium text-foreground">{t("howTitle")}</summary>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-muted">
            <li>{t("howStep1")}</li>
            <li>{t("howStep2")}</li>
            <li>{t("howStep3")}</li>
          </ol>
        </details>
      )}
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
