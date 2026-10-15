"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { getProject, deleteProject, listJobs, listModels, Project, ImportJob } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RequireAuth } from "@/components/RequireAuth";
import { Badge, statusColor } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Spinner, ErrorBanner } from "@/components/ui/Feedback";

interface Step {
  key: string;
  href: string;
  leadOnly?: boolean;
  optional?: boolean;
  ctaKey: string;
}

/**
 * Parcours par défaut : import → analyse (question + modèles en cascade) → résultats / relecture.
 * Optionnel : gold + benchmark (mesurer / comparer des modèles) et prompts (réglage avancé).
 */
const STEPS: Step[] = [
  { key: "import", href: "import", ctaKey: "ctaImport" },
  { key: "inference", href: "inference", leadOnly: true, ctaKey: "ctaInference" },
  { key: "articles", href: "articles", ctaKey: "ctaArticles" },
  { key: "gold", href: "gold", leadOnly: true, optional: true, ctaKey: "ctaGold" },
  { key: "benchmarks", href: "benchmarks", leadOnly: true, optional: true, ctaKey: "ctaBenchmarks" },
  { key: "prompts", href: "prompts", leadOnly: true, optional: true, ctaKey: "ctaPrompts" },
];

/** Étape suivante selon l'avancement du projet. */
function nextStepKey(project: Project): string {
  if (project.status === "DRAFT" || project.status === "PROCESSING") return "import";
  if (project.status === "FROZEN") return "inference";
  return "articles"; // COMPLETED : tri terminé → résultats / relecture
}

function ProjectDashboard() {
  const t = useTranslations("project");
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  const [project, setProject] = useState<Project | null>(null);
  const [jobs, setJobs] = useState<ImportJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [modelCount, setModelCount] = useState<number | null>(null);
  const { user } = useAuth();

  const load = useCallback(async () => {
    setError(null);
    try {
      const [p, js] = await Promise.all([getProject(projectId), listJobs(projectId)]);
      setProject(p);
      setJobs(js);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    listModels(projectId).then((m) => setModelCount(m.length)).catch(() => setModelCount(null));
  }, [load, projectId]);

  async function handleDelete() {
    if (!window.confirm(t("deleteConfirm"))) return;
    setDeleting(true);
    try {
      await deleteProject(projectId);
      window.location.href = "/projects";
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
      setDeleting(false);
    }
  }

  if (error) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!project) return <Spinner label={t("loading")} />;

  const isLead = project.role_in_project === "LEAD";
  const visibleSteps = STEPS.filter((s) => !s.leadOnly || isLead);
  const nextKey = nextStepKey(project);
  const articleCount = project.article_count ?? 0;
  const imports = (jobs ?? []).filter((j) => j.kind !== "fulltext");
  const lastJob = imports.length > 0 ? imports[imports.length - 1] : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold text-foreground">{project.title}</h1>
        </div>
        <div className="flex items-center gap-2">
          <Badge color={statusColor(project.status)}>{project.status}</Badge>
          <Button variant="danger" size="sm" loading={deleting} onClick={handleDelete}>
            {t("deleteProject")}
          </Button>
        </div>
      </div>

      {project.description && (
        <p className="text-sm text-muted">{project.description}</p>
      )}

      {project.status === "PROCESSING" && (
        <div className="rounded-lg border border-primary bg-primary/10 p-3 text-sm">
          <Link
            href={`/projects/${projectId}/import`}
            className="font-medium text-primary hover:underline"
          >
            {t("importInProgress")} →
          </Link>
        </div>
      )}

      {/* État du corpus + dernière import + étape suivante */}
      <div className="rounded-lg border border-border bg-surface-muted p-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-muted">
            {t("corpus")} : <strong className="text-foreground">{articleCount}</strong>{" "}
            {t("articles")}
          </span>
          <span className="font-medium text-primary">
            {t("nextStep")} : {t(nextKey)}
          </span>
        </div>
        {lastJob && (
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <span>
              {t("lastImport")} : <code className="text-foreground">{lastJob.query}</code>
            </span>
            {lastJob.result && (
              <span>
                {t("imported")} : {String(lastJob.result.imported ?? 0)} · {t("failed")} :{" "}
                {String(lastJob.result.failed ?? 0)}
              </span>
            )}
            {lastJob.created_at && (
              <span>{new Date(lastJob.created_at).toLocaleString()}</span>
            )}
          </div>
        )}
      </div>

      {/* Modèles de tri : prérequis du benchmark et de l'inférence */}
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

      <details className="rounded-lg border border-border bg-surface p-3 text-sm">
        <summary className="cursor-pointer font-medium text-foreground">{t("howTitle")}</summary>
        <p className="mt-2 text-muted">{t("howBody")}</p>
      </details>

      {/* Parcours par défaut puis étapes optionnelles */}
      {[false, true].map((optional) => {
        const steps = visibleSteps.filter((s) => Boolean(s.optional) === optional);
        if (steps.length === 0) return null;
        return (
          <section key={String(optional)} className="flex flex-col gap-3">
            {optional && (
              <div>
                <h2 className="text-sm font-semibold text-foreground">{t("optionalTitle")}</h2>
                <p className="text-xs text-muted">{t("optionalHint")}</p>
              </div>
            )}
            <ol className="flex flex-col gap-3">
              {steps.map((s, i) => {
                const isNext = s.key === nextKey;
                // Tout sauf l'import exige un corpus figé (import terminé).
                const blocked =
                  s.key !== "import" &&
                  project.status !== "FROZEN" &&
                  project.status !== "COMPLETED";
                return (
                  <li
                    key={s.key}
                    className={`rounded-lg border p-4 transition-shadow ${
                      isNext
                        ? "border-primary bg-primary/5 shadow-sm"
                        : "border-border bg-surface hover:shadow-md"
                    } ${optional ? "opacity-90" : ""}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-start gap-3">
                        {!optional && (
                          <span
                            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
                              isNext ? "bg-primary text-primary-foreground" : "bg-surface-muted text-muted"
                            }`}
                          >
                            {i + 1}
                          </span>
                        )}
                        <div>
                          <h3 className="font-semibold text-foreground">{t(s.key)}</h3>
                          <p className="mt-0.5 text-sm text-muted">{t(`${s.key}Hint`)}</p>
                          {blocked && <p className="mt-1 text-xs text-warning">{t("needsCorpus")}</p>}
                        </div>
                      </div>
                      {blocked ? (
                        <Button variant="secondary" size="sm" disabled className="shrink-0">
                          {t(s.ctaKey)}
                        </Button>
                      ) : (
                        <Link href={`/projects/${projectId}/${s.href}`} className="shrink-0">
                          <Button variant={isNext ? "primary" : "secondary"} size="sm">
                            {t(s.ctaKey)}
                          </Button>
                        </Link>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          </section>
        );
      })}
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
