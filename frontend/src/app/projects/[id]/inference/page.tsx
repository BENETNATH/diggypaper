"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  updateProject,
  triggerInference,
  getInference,
  getProject,
  listModels,
  InferenceJob,
  ModelInfo,
  SchemaField,
  isScorer,
} from "@/lib/api";
import { ExportMenu } from "@/components/ExportMenu";
import { RequireAuth } from "@/components/RequireAuth";
import { QuestionEditor } from "@/components/QuestionEditor";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Field";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { ErrorBanner, ProgressBar } from "@/components/ui/Feedback";

const num = (v: unknown): number => (typeof v === "number" ? v : 0);

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className={`rounded-lg p-2 text-center ${tone ?? "bg-surface-muted"}`}>
      <div className="text-lg font-bold text-foreground">{value.toLocaleString()}</div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  );
}

function InferenceContent() {
  const t = useTranslations("inference");
  const projectId = useParams<{ id: string }>().id;

  const [job, setJob] = useState<InferenceJob | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState<{
    question: string;
    fields: SchemaField[];
    sections: string[];
    readWindows: number;
  } | null>(null);
  const [articleCount, setArticleCount] = useState(0);
  const [stage1, setStage1] = useState("");
  const [stage2, setStage2] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [launched, setLaunched] = useState(false);
  const progressRef = useRef<HTMLDivElement>(null);

  const active = job?.status === "PENDING" || job?.status === "RUNNING";
  const label = (m: ModelInfo) =>
    `${m.name} — ${isScorer(m) ? t("kindDecision") : t("kindGenerative")}${
      m.ram_usage_mb ? ` · ~${m.ram_usage_mb} MB` : ""
    }`;

  const load = useCallback(async () => {
    try {
      setJob(await getInference(projectId));
      setError(null);
    } catch (e) {
      if (e instanceof Error && /404/.test(e.message)) setJob(null);
      else setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    getProject(projectId)
      .then((p) => {
        setQ({
          question: p.question ?? "",
          fields: p.fields ?? [],
          sections: p.text_sections ?? ["methods"],
          readWindows: p.read_windows ?? 4,
        });
        setArticleCount(p.article_count ?? 0);
      })
      .catch(() => {});
    listModels(projectId).then(setModels).catch(() => {});
  }, [load, projectId]);

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => void load(), 3000);
    return () => clearInterval(id);
  }, [active, load]);

  // Sélection par défaut : pas d'étage 1 ; étage 2 = modèle le plus léger dont la RAM est connue.
  useEffect(() => {
    if (models.length === 0 || stage2) return;
    const known = models.filter((m) => m.ram_usage_mb).sort((a, b) => a.ram_usage_mb! - b.ram_usage_mb!);
    setStage2((known[0] ?? models[0]).name);
  }, [models, stage2]);

  /** Reprend une analyse interrompue : les articles déjà analysés sont sautés. */
  async function handleResume() {
    if (!job) return;
    setSubmitting(true);
    setError(null);
    try {
      await triggerInference(projectId, {
        model: job.model_used,
        ...(job.prefilter_model ? { prefilter_model: job.prefilter_model } : {}),
        ...(job.prompt_template_id ? { prompt_template_id: job.prompt_template_id } : {}),
        resume: true,
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    if ((!stage2 && !stage1) || !q) return;
    setSubmitting(true);
    setError(null);
    try {
      // La question est enregistrée au lancement (une seule étape pour l'utilisateur).
      await updateProject(projectId, {
        question: q.question,
        fields: q.fields.map((f) => ({
          ...f,
          options: f.type === "choice" ? (f.options ?? []).filter(Boolean) : undefined,
        })),
        text_sections: q.sections,
        read_windows: q.readWindows,
      });
      // Étage 2 vide : l'étage 1 décide seul.
      await triggerInference(projectId, {
        model: stage2 || stage1,
        ...(stage2 && stage1 && stage2 !== stage1 ? { prefilter_model: stage1 } : {}),
      });
      await load();
      setLaunched(true);
      // La zone de progression est sous la ligne de flottaison : on y amène l'utilisateur.
      setTimeout(() => progressRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setSubmitting(false);
    }
  }

  const r = job?.result ?? {};
  const total = num(r.total);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("intro")}</p>
      </div>

      {error && <ErrorBanner message={error} />}

      <Card title={`1. ${t("questionTitle")}`}>
        {q ? (
          <QuestionEditor {...q} articleCount={articleCount} onChange={setQ} />
        ) : (
          <p className="text-sm text-muted">…</p>
        )}
      </Card>

      <Card title={`2. ${t("cascadeTitle")}`}>
        {models.length === 0 ? (
          <p className="text-sm text-warning">
            {t("noModels")}{" "}
            <Link href="/models" className="font-medium text-primary hover:underline">
              {t("manageModels")} →
            </Link>
          </p>
        ) : (
          <form onSubmit={handleRun} className="flex flex-col gap-4">
            <div className="grid gap-3 md:grid-cols-[1fr_auto_1fr] md:items-start">
              <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
                <p className="text-sm font-semibold text-foreground">{t("stage1")}</p>
                <p className="text-xs text-muted">{t("stage1Why")}</p>
                <Select value={stage1} onChange={(e) => setStage1(e.target.value)} disabled={active}>
                  <option value="">{t("noPrefilter")}</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.name}>
                      {label(m)}
                    </option>
                  ))}
                </Select>
                <p className="text-xs text-muted">{t("stage1Which")}</p>
              </div>
              <span className="hidden self-center text-2xl text-muted md:block" aria-hidden>
                →
              </span>
              <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
                <p className="text-sm font-semibold text-foreground">{t("stage2")}</p>
                <p className="text-xs text-muted">{t("stage2Why")}</p>
                <Select value={stage2} onChange={(e) => setStage2(e.target.value)} disabled={active}>
                  {stage1 && <option value="">{t("noStage2")}</option>}
                  {models.map((m) => (
                    <option key={m.id} value={m.name}>
                      {label(m)}
                    </option>
                  ))}
                </Select>
                <p className="text-xs text-muted">{t("stage2Which")}</p>
              </div>
            </div>
            <p className="text-xs text-muted">{t("routingHint")}</p>
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" loading={submitting} disabled={active || (!stage2 && !stage1) || !q || q.sections.length === 0}>
                {job ? t("rerun") : t("run")}
              </Button>
              {launched && active && <span className="text-sm text-success">✓ {t("launched")}</span>}
            </div>
          </form>
        )}
      </Card>

      {job && (
        <div ref={progressRef}>
        <Card title={`3. ${t("progressTitle")}`}>
          <div className="mb-3 flex items-center justify-between gap-3 text-sm text-muted">
            <span className="font-mono">{job.model_used}</span>
            <Badge color={statusColor(job.status)}>{job.status}</Badge>
          </div>
          {active && (
            <ProgressBar
              value={total > 0 ? (num(r.done) / total) * 100 : null}
              label={total > 0 ? t("progress", { done: num(r.done), total }) : t("starting")}
            />
          )}
          {active && num(r.done) === 0 && <p className="mt-2 text-xs text-muted">{t("firstRunNote")}</p>}
          {(active ? total > 0 : job.status === "COMPLETED") && (
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label={t("processed")} value={num(r.processed ?? r.done)} />
              <Stat label={t("autoAccepted")} value={num(r.auto_accepted)} tone="bg-success/10" />
              <Stat label={t("autoRejected")} value={num(r.auto_rejected)} tone="bg-danger/10" />
              <Stat label={t("needsReview")} value={num(r.needs_review)} tone="bg-warning/10" />
            </div>
          )}
          {num(r.prefiltered) > 0 && (
            <p className="mt-2 text-xs text-muted">{t("prefilteredCount", { n: num(r.prefiltered) })}</p>
          )}
          {num(r.invalid_json) > 0 && (
            <p className="mt-2 text-xs text-warning">{t("invalidJson", { n: num(r.invalid_json) })}</p>
          )}
          {job.status === "FAILED" && job.error && (
            <p className="mt-3 rounded-lg bg-danger/10 p-3 text-sm text-danger">{job.error}</p>
          )}
          {job.status === "FAILED" && (
            <div className="mt-3 flex flex-col items-start gap-1">
              <Button loading={submitting} onClick={handleResume}>
                ↻ {t("resume")}
              </Button>
              <span className="text-xs text-muted">{t("resumeHint", { n: num(r.done) })}</span>
            </div>
          )}
          {job.status === "COMPLETED" && (
            <div className="mt-4 flex flex-wrap gap-2">
              <Link href={`/projects/${projectId}/articles`}>
                <Button variant="secondary">{t("seeResults")}</Button>
              </Link>
              <ExportMenu projectId={projectId} />
              {num(r.needs_review) > 0 && (
                <Link href={`/projects/${projectId}/annotate?queue=review`}>
                  <Button>{t("annotateReview")}</Button>
                </Link>
              )}
            </div>
          )}
        </Card>
        </div>
      )}
    </div>
  );
}

export default function InferencePage() {
  return (
    <RequireAuth>
      <InferenceContent />
    </RequireAuth>
  );
}
