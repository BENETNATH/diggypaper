"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { listBenchmarks, listModels, cancelBenchmark, BenchmarkRun, ModelInfo } from "@/lib/api";
import { currentSearch } from "@/lib/search";
import { useSearchId, useSearches } from "@/lib/useSearch";
import { BenchmarkForm } from "@/components/BenchmarkForm";
import { BenchmarkRunCard } from "@/components/BenchmarkRunCard";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Skeleton, ErrorBanner, EmptyState } from "@/components/ui/Feedback";

function BenchmarksContent() {
  const t = useTranslations("benchmarks");
  const projectId = useParams<{ id: string }>().id;
  const searchId = useSearchId(projectId);
  const { searches } = useSearches(projectId, searchId);

  const [runs, setRuns] = useState<BenchmarkRun[] | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

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

  async function handleStop(runId: string) {
    if (!window.confirm(t("stopConfirm"))) return;
    setError(null);
    try {
      await cancelBenchmark(projectId, runId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    }
  }

  if (error && !runs) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!runs || !searches) return <Skeleton rows={3} />;

  // Plus récent d'abord.
  const ordered = [...runs].reverse();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
          <p className="mt-1 text-sm text-muted">{t("intro")}</p>
        </div>
        <Button onClick={() => setShowForm((v) => !v)} disabled={hasActive}>
          {t("new")}
        </Button>
      </div>
      {hasActive && <p className="text-xs text-muted">{t("oneAtATime")}</p>}

      {error && <ErrorBanner message={error} />}

      {showForm && searches.length > 0 && (
        <BenchmarkForm
          projectId={projectId}
          searches={searches}
          models={models}
          defaultSearchId={currentSearch(searches, searchId)?.id}
          onLaunched={() => {
            setShowForm(false);
            void load();
          }}
          onCancel={() => setShowForm(false)}
        />
      )}

      {ordered.length === 0 ? (
        <EmptyState title={t("empty")} hint={t("emptyHint")} />
      ) : (
        <div className="flex flex-col gap-3">
          {ordered.map((run) => (
            <BenchmarkRunCard
              key={run.id}
              projectId={projectId}
              run={run}
              search={currentSearch(searches, run.search_id ?? undefined)}
              models={models}
              analysisSearchId={searchId}
              onStop={(id) => void handleStop(id)}
            />
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
