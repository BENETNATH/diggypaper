"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  listBenchmarks,
  triggerBenchmark,
  listPrompts,
  listModels,
  BenchmarkRun,
  PromptTemplate,
  ModelInfo,
} from "@/lib/api";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Field, Select } from "@/components/ui/Field";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { Skeleton, ErrorBanner, EmptyState, ProgressBar } from "@/components/ui/Feedback";

function pct(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(1)} %`;
}

function BenchmarksContent() {
  const t = useTranslations("benchmarks");
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  const [runs, setRuns] = useState<BenchmarkRun[] | null>(null);
  const [prompts, setPrompts] = useState<PromptTemplate[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [promptId, setPromptId] = useState("");
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const hasActive = (runs ?? []).some((r) => r.status === "PENDING" || r.status === "RUNNING");

  const load = useCallback(async () => {
    setError(null);
    try {
      setRuns(await listBenchmarks(projectId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    listPrompts(projectId).then(setPrompts).catch(() => {});
    listModels(projectId).then(setModels).catch(() => {});
  }, [load, projectId]);

  // Polling uniquement pendant qu'un run est actif (pas de boucle inutile).
  useEffect(() => {
    if (!hasActive) return;
    const id = setInterval(() => {
      void load();
    }, 5000);
    return () => clearInterval(id);
  }, [hasActive, load]);

  function toggleModel(name: string) {
    setSelectedModels((prev) =>
      prev.includes(name) ? prev.filter((m) => m !== name) : [...prev, name]
    );
  }

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    if (selectedModels.length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      await triggerBenchmark(projectId, {
        ...(promptId ? { prompt_template_id: promptId } : {}),
        models: selectedModels,
      });
      setShowForm(false);
      setPromptId("");
      setSelectedModels([]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setSubmitting(false);
    }
  }

  if (error && !runs) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!runs) return <Skeleton rows={3} />;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <Button onClick={() => setShowForm((v) => !v)}>{t("new")}</Button>
      </div>

      {error && <ErrorBanner message={error} />}

      {showForm && (
        <Card title={t("new")}>
          {models.length === 0 && (
            <p className="mb-3 rounded-lg bg-warning/10 p-3 text-sm text-foreground">
              {t("noModels")}{" "}
              <Link href="/models" className="font-medium text-primary hover:underline">
                {t("manageModels")} →
              </Link>
            </p>
          )}
          <form onSubmit={handleRun} className="flex flex-col gap-4">
            <Field label={t("prompt")}>
              <Select value={promptId} onChange={(e) => setPromptId(e.target.value)}>
                <option value="">{t("autoPrompt")}</option>
                {prompts.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.version_number} {p.title ?? ""}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={t("models")}>
              <div className="flex flex-col gap-2">
                {models.map((m) => (
                  <label key={m.id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="h-4 w-4 rounded border-border"
                      checked={selectedModels.includes(m.name)}
                      onChange={() => toggleModel(m.name)}
                    />
                    <span>{m.name}</span>
                  </label>
                ))}
              </div>
            </Field>
            <div className="flex gap-2">
              <Button type="submit" loading={submitting} disabled={selectedModels.length === 0}>
                {t("run")}
              </Button>
              <Button type="button" variant="secondary" onClick={() => setShowForm(false)}>
                {t("cancel")}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {runs.length === 0 ? (
        <EmptyState title={t("empty")} hint={t("emptyHint")} />
      ) : (
        <div className="flex flex-col gap-3">
          {runs.map((run) => (
            <Card key={run.id}>
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 text-sm text-muted">
                  <span className="font-mono">{run.models_tested?.join(", ") ?? "—"}</span>
                  {run.created_at && (
                    <span className="ml-2 text-muted/70">
                      {new Date(run.created_at).toLocaleString()}
                    </span>
                  )}
                </div>
                <Badge color={statusColor(run.status)}>{run.status}</Badge>
              </div>
              {(run.status === "PENDING" || run.status === "RUNNING") && (
                <div className="mt-3">
                  <ProgressBar
                    value={run.progress ? (run.progress.done / run.progress.total) * 100 : null}
                    label={
                      run.progress
                        ? t("progress", {
                            done: run.progress.done,
                            total: run.progress.total,
                            model: run.progress.model ?? "",
                          })
                        : t("starting")
                    }
                  />
                </div>
              )}
              {run.results && run.results.length > 0 && (
                <div className="mt-3 flex flex-col gap-2">
                  {run.results.map((r) => (
                    <div
                      key={r.model_name}
                      className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-lg bg-surface-muted p-3 text-sm sm:grid-cols-6"
                    >
                      <span className="font-medium text-foreground">{r.model_name}</span>
                      <span>
                        {t("accuracy")} : <b>{pct(r.accuracy)}</b>
                      </span>
                      <span>
                        {t("precision")} : <b>{pct(r.precision_score)}</b>
                      </span>
                      <span>
                        {t("recall")} : <b>{pct(r.recall_score)}</b>
                      </span>
                      <span>
                        {t("f1")} : <b>{pct(r.f1_score)}</b>
                      </span>
                      <span>
                        {t("time")} : <b>{r.execution_time_seconds?.toFixed(1) ?? "—"} s</b>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

export default function BenchmarksPage() {
  return (
    <RequireAuth>
      <BenchmarksContent />
    </RequireAuth>
  );
}
