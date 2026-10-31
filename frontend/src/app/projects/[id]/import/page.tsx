"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  queryVolume,
  triggerImport,
  completeFulltext,
  listJobs,
  getProject,
  ImportJob,
  Project,
} from "@/lib/api";
import { withSearch } from "@/lib/search";
import { useSearchId } from "@/lib/useSearch";
import { RequireAuth } from "@/components/RequireAuth";
import { ReferencesImport } from "@/components/ReferencesImport";
import { CitationExpand } from "@/components/CitationExpand";
import { QueryAssistant } from "@/components/QueryAssistant";
import { sourceLabel, sourceName } from "@/lib/source";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { ErrorBanner, ProgressBar } from "@/components/ui/Feedback";

const num = (v: unknown): number => (typeof v === "number" ? v : 0);

/** Progression lisible d'un job : phase, pourcentage, compteurs. */
function JobProgress({ job, onRetry }: { job: ImportJob; onRetry?: (job: ImportJob) => void }) {
  const t = useTranslations("import");
  const projectId = useParams<{ id: string }>().id;
  const r = job.result ?? {};
  const active = job.status === "PENDING" || job.status === "RUNNING";
  const total = num(r.total);
  const done = num(r.done);

  let label = t("phasePending");
  let value: number | null = null;
  if (job.kind === "fulltext") {
    if (job.status === "RUNNING" && total > 0) {
      value = (done / total) * 100;
      label = t("phaseFulltext", { done, total, found: num(r.found) });
    }
  } else if (job.status === "RUNNING") {
    if (r.phase === "linking" && total > 0) {
      value = (done / total) * 100;
      label = t("phaseLinking", { done, total });
    } else if (r.phase === "importing" && total > 0) {
      value = (done / total) * 100;
      label = t("phaseImporting", { done, total });
    } else {
      label = t("phaseSearching");
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <code className="min-w-0 truncate text-xs text-muted">
          {job.kind === "fulltext" ? t("fulltextJob") : job.kind === "references" ? t("refsJob") : job.kind === "citations" ? t("citJob") : job.query}
        </code>
        <Badge color={statusColor(job.status)}>{job.status}</Badge>
      </div>
      {active && <ProgressBar value={value} label={label} />}
      {job.kind === "fulltext" && job.status === "COMPLETED" && (
        <p className="text-sm text-foreground">
          {t("fulltextDone", { found: num(r.found), total })}
          {typeof r.sources === "object" && r.sources !== null && (
            <span className="text-muted">
              {" "}
              ({Object.entries(r.sources as Record<string, number>).map(([k, n]) => `${k}: ${n}`).join(", ")})
            </span>
          )}
        </p>
      )}
      {job.kind !== "fulltext" && (job.status === "COMPLETED" || num(r.imported) > 0 || num(r.failed) > 0) && (
        <dl className="grid grid-cols-2 gap-2 text-center sm:grid-cols-4">
          {[
            ["imported", num(r.imported)],
            ["fulltext", num(r.fulltext)],
            ["failed", num(r.failed)],
            ["total", num(r.total_count)],
          ].map(([k, v]) => (
            <div key={k as string} className="rounded-lg bg-surface-muted p-2">
              <dd className="text-lg font-bold text-foreground">{(v as number).toLocaleString()}</dd>
              <dt className="text-xs text-muted">{t(k as string)}</dt>
            </div>
          ))}
        </dl>
      )}
      {job.kind === "citations" && job.status === "COMPLETED" && (
        <div className="flex flex-col gap-1 text-sm">
          <span className="text-muted">{t("citDone", { found: num(r.candidates), n: num(r.imported) })}</span>
          {typeof r.search_id === "string" && (
            <Link
              href={withSearch(`/projects/${projectId}/inference`, r.search_id)}
              className="font-medium text-primary hover:underline"
            >
              {t("citAnalyse", { n: num(r.imported) })} →
            </Link>
          )}
        </div>
      )}
      {job.kind === "references" && job.status === "COMPLETED" && (
        <div className="flex flex-col gap-1 text-xs text-muted">
          {typeof r.sources === "object" && r.sources !== null && (
            <span>
              {t("refsSources", {
                pubmed: num((r.sources as Record<string, unknown>).pubmed),
                crossref: num((r.sources as Record<string, unknown>).crossref),
                file: num((r.sources as Record<string, unknown>).file),
              })}
              {num(r.duplicates) > 0 && ` · ${t("refsDuplicates", { n: num(r.duplicates) })}`}
            </span>
          )}
          {Array.isArray(r.failed_items) && r.failed_items.length > 0 && (
            <details>
              <summary className="cursor-pointer text-danger">
                {t("refsFailed", { n: r.failed_items.length })}
              </summary>
              <ul className="mt-1 max-h-40 overflow-y-auto font-mono">
                {(r.failed_items as string[]).map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {job.status === "COMPLETED" && r.truncated === true && (
        <p className="text-sm text-warning">{t("truncated", { n: num(r.imported).toLocaleString() })}</p>
      )}
      {job.kind === "import" && job.status === "COMPLETED" && num(r.imported) > num(r.fulltext) && (
        <p className="text-xs text-muted">{t("fulltextHint")}</p>
      )}
      {job.status === "FAILED" && job.error && (
        <p className="rounded-lg bg-danger/10 p-3 text-sm text-danger">{job.error}</p>
      )}
      {job.status === "FAILED" && onRetry && job.kind !== "references" && job.kind !== "citations" && job.kind !== "crate" && (
        <div className="flex flex-col items-start gap-1">
          <Button size="sm" onClick={() => onRetry(job)}>
            ↻ {t("retry")}
          </Button>
          <span className="text-xs text-muted">{t("retryHint")}</span>
        </div>
      )}
    </div>
  );
}

function ImportContent() {
  const t = useTranslations("import");
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const searchId = useSearchId(projectId);

  const [project, setProject] = useState<Project | null>(null);
  const [jobs, setJobs] = useState<ImportJob[] | null>(null);
  const [mode, setMode] = useState<"pubmed" | "refs" | "cites">("pubmed");
  const [source, setSource] = useState<"pubmed" | "europepmc" | "openalex" | "hal">("pubmed");
  const [query, setQuery] = useState("");
  const [volume, setVolume] = useState<number | null>(null);
  const [cap, setCap] = useState(Infinity); // plafond d'import de l'instance (illimité : au-delà de 1e9)
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [watching, setWatching] = useState(false); // suivi actif (import lancé ou en cours)
  const [error, setError] = useState<string | null>(null);
  const jobsBefore = useRef(0);

  const load = useCallback(async () => {
    try {
      const [p, js] = await Promise.all([getProject(projectId), listJobs(projectId)]);
      setProject(p);
      setJobs(js);
      // le layout (onglets) ne se recharge pas sur place : on lui donne l'état à jour
      window.dispatchEvent(new CustomEvent("diggy:project", { detail: p }));
      return js;
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
      return null;
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const activeJob = jobs?.find((j) => j.status === "PENDING" || j.status === "RUNNING") ?? null;
  const polling = watching;

  // Reprise du suivi si un import tourne déjà (page rechargée / revisitée).
  useEffect(() => {
    if (activeJob && !watching) setWatching(true);
  }, [activeJob, watching]);

  // Suivi : boucle auto-replanifiée, indépendante de l'état React. Elle ne s'arrête que
  // lorsqu'aucun import n'est actif ET que le job lancé est apparu (ou après 30 s).
  useEffect(() => {
    if (!watching) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const started = Date.now();
    const tick = async () => {
      const js = await load();
      if (stopped) return;
      const active = js?.some((j) => j.status === "PENDING" || j.status === "RUNNING") ?? true;
      const appeared = (js?.length ?? 0) > jobsBefore.current;
      if (!active && (appeared || Date.now() - started > 30000)) {
        setWatching(false);
        return;
      }
      timer = setTimeout(tick, 2000);
    };
    timer = setTimeout(tick, 1000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [watching, load]);

  const lastJob = jobs && jobs.length > 0 ? jobs[jobs.length - 1] : null;
  const locked = project?.status === "PROCESSING";
  const hasCorpus = (project?.article_count ?? 0) > 0;

  async function handleCheckVolume(e: React.FormEvent) {
    e.preventDefault();
    setChecking(true);
    setError(null);
    try {
      const r = await queryVolume(projectId, query, source);
      setVolume(r.count);
      setCap(r.max >= 1e9 ? Infinity : r.max);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "error";
      const name = sourceName(msg.replace(/_unavailable$/, ""));
      setError(name ? t("srcDown", { src: name }) : msg);
    } finally {
      setChecking(false);
    }
  }

  async function handleFulltext() {
    setError(null);
    try {
      jobsBefore.current = jobs?.length ?? 0;
      await completeFulltext(projectId);
      setWatching(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    }
  }

  /** Relance un job échoué (requête + source identiques : les articles déjà importés
   *  sont ignorés). */
  async function handleRetry(failed: ImportJob) {
    if (failed.kind === "fulltext") return handleFulltext();
    if (failed.kind === "references" || failed.kind === "citations" || failed.kind === "crate") return;
    setError(null);
    try {
      jobsBefore.current = jobs?.length ?? 0;
      await triggerImport(projectId, failed.query ?? "", failed.source ?? "pubmed");
      setWatching(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    }
  }

  async function handleImport() {
    if (volume !== null && volume > 1000 && !window.confirm(t("confirmBig", { n: volume }))) return;
    setSubmitting(true);
    setError(null);
    try {
      jobsBefore.current = jobs?.length ?? 0;
      await triggerImport(projectId, query, source);
      setWatching(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("intro")}</p>
        <p className="mt-1 text-sm text-muted">{t("backgroundNote")}</p>
      </div>

      {error && <ErrorBanner message={error} />}

      {/* Import en cours / dernier import */}
      {(watching || lastJob) && (
        <Card title={t("jobTitle")}>
          {watching && !activeJob && jobs?.length === jobsBefore.current ? (
            <ProgressBar value={null} label={t("phasePending")} />
          ) : lastJob ? (
            <JobProgress job={lastJob} onRetry={!activeJob && !watching ? handleRetry : undefined} />
          ) : null}
          {lastJob?.status === "COMPLETED" && !polling && (
            <Link
              href={withSearch(`/projects/${projectId}/articles`, searchId)}
              className="mt-3 inline-block text-sm font-medium text-primary hover:underline"
            >
              {t("viewArticles")} →
            </Link>
          )}
        </Card>
      )}

      {/* Textes intégraux : complément par les sources ouvertes (Europe PMC, Unpaywall, préprints) */}
      {project && (project.status === "FROZEN" || project.status === "COMPLETED") && (
        <Card title={t("fulltextTitle")}>
          <p className="text-sm text-foreground">
            {t("fulltextCount", { n: project.fulltext_count ?? 0, total: project.article_count ?? 0 })}
          </p>
          <p className="mt-1 text-xs text-muted">{t("fulltextExplain")}</p>
          <Button
            className="mt-3"
            variant="secondary"
            onClick={handleFulltext}
            disabled={polling || !!activeJob || (project.fulltext_count ?? 0) >= (project.article_count ?? 0)}
          >
            {t("fulltextButton")}
          </Button>
        </Card>
      )}

      {/* 1. Requête */}
      <Card title={hasCorpus ? t("queryTitleMore") : t("queryTitle")}>
        {locked ? (
          <p className="text-sm text-muted">{t("lockedProcessing")}</p>
        ) : (
          <>
            <div role="tablist" className="mb-4 inline-flex rounded-lg border border-border p-0.5 text-sm">
              {(["pubmed", "refs", ...(hasCorpus ? (["cites"] as const) : [])] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="tab"
                  aria-selected={mode === m}
                  onClick={() => setMode(m)}
                  className={`rounded-md px-3 py-1 ${
                    mode === m ? "bg-primary text-white" : "text-muted hover:text-foreground"
                  }`}
                >
                  {t(
                    m === "pubmed" ? "modeSearch" : m === "refs" ? "modeRefs" : "modeCites",
                    m === "pubmed" ? { src: sourceLabel(source, null, "") ?? "" } : undefined,
                  )}
                </button>
              ))}
            </div>
            {mode === "cites" ? (
              <CitationExpand
                projectId={projectId}
                onStarted={() => {
                  jobsBefore.current = jobs?.length ?? 0;
                  setWatching(true);
                }}
              />
            ) : mode === "refs" ? (
              <ReferencesImport
                projectId={projectId}
                onStarted={() => {
                  jobsBefore.current = jobs?.length ?? 0;
                  setWatching(true);
                }}
              />
            ) : (
            <div className="flex flex-col gap-4">
            <details className="rounded-lg border border-border p-3">
              <summary className="cursor-pointer text-sm font-medium text-foreground">{t("qaTitle")}</summary>
              <div className="mt-3">
                <QueryAssistant
                  key={source}
                  projectId={projectId}
                  source={source}
                  onUse={(q) => {
                    setQuery(q);
                    setVolume(null);
                  }}
                />
              </div>
            </details>
          <form onSubmit={handleCheckVolume} className="flex flex-col gap-4">
            {hasCorpus && <p className="text-sm text-muted">{t("addHint", { n: project?.article_count ?? 0 })}</p>}
            <fieldset className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              <legend className="mb-1 font-medium text-foreground">{t("sourceLabel")}</legend>
              {(["pubmed", "europepmc", "openalex", "hal"] as const).map((s) => (
                <label key={s} className="flex items-center gap-1.5">
                  <input
                    type="radio"
                    name="import-source"
                    value={s}
                    checked={source === s}
                    onChange={() => {
                      setSource(s);
                      setVolume(null);
                    }}
                  />
                  {sourceLabel(s, null, "")}
                </label>
              ))}
            </fieldset>
            <Field label={t("queryLabel")} hint={source === "pubmed" ? t("queryHint") : t("queryHintGeneric")}>
              <Input
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setVolume(null);
                }}
                required
                placeholder='"mouse" AND "behavior"'
              />
            </Field>
            {volume !== null && (
              <p className="rounded-lg bg-surface-muted p-3 text-sm text-foreground">
                {t("volumeResult")} : <strong>{volume.toLocaleString()}</strong>
                {volume > cap && <span className="text-warning"> — {t("capNote", { max: cap.toLocaleString() })}</span>}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button type="submit" variant="secondary" loading={checking} disabled={!query}>
                {t("checkVolume")}
              </Button>
              <Button
                type="button"
                onClick={handleImport}
                loading={submitting}
                disabled={!query || volume === null || volume === 0 || polling}
              >
                {hasCorpus ? t("importMore") : t("import")}
              </Button>
            </div>
            {volume === null && query && (
              <p className="text-xs text-muted">{t("checkFirst")}</p>
            )}
          </form>
            </div>
            )}
          </>
        )}
      </Card>

      {/* Historique */}
      {jobs && jobs.length > 1 && (
        <Card title={t("historyTitle")}>
          <ul className="divide-y divide-border">
            {[...jobs].reverse().slice(1).map((j) => (
              <li key={j.id} className="py-3">
                <JobProgress job={j} />
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

export default function ImportPage() {
  return (
    <RequireAuth>
      <ImportContent />
    </RequireAuth>
  );
}
