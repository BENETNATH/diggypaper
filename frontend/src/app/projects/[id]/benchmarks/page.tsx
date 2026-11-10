"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  getProject,
  listBenchmarks,
  listModels,
  cancelBenchmark,
  deleteBenchmark,
  rerunBenchmark,
  retryBenchmarkErrors,
  BenchmarkRun,
  ModelInfo,
  Project,
} from "@/lib/api";
import { currentSearch } from "@/lib/search";
import { useSearchId, useSearches } from "@/lib/useSearch";
import { useApiError } from "@/lib/errorMsg";
import { useAuth } from "@/lib/auth";
import { isProjectLead } from "@/lib/role";
import { BenchmarkForm } from "@/components/BenchmarkForm";
import { BenchmarkRunCard } from "@/components/BenchmarkRunCard";
import { ComparisonPanel } from "@/components/ComparisonPanel";
import { AgreementPanel } from "@/components/AgreementPanel";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Skeleton, ErrorBanner, EmptyState } from "@/components/ui/Feedback";
import { Modal } from "@/components/ui/Modal";

function BenchmarksContent() {
  const t = useTranslations("benchmarks");
  const tp = useTranslations("project");
  const tc = useTranslations("common");
  const toError = useApiError();
  const { user } = useAuth();
  const projectId = useParams<{ id: string }>().id;
  const searchId = useSearchId(projectId);
  const { searches, current } = useSearches(projectId, searchId);
  const runParam = useSearchParams().get("run"); // deep-link from the analyses list

  const [runs, setRuns] = useState<BenchmarkRun[] | null>(null);
  // spec clarte-2 R3: the corpus size gates the page-level empty state
  const [project, setProject] = useState<Project | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  // Concordance scope (spec 2026-11-08): one search selector for both panels.
  const [concChoice, setConcChoice] = useState<string | null>(null);

  const hasActive = (runs ?? []).some((r) => r.status === "PENDING" || r.status === "RUNNING");

  const load = useCallback(() => {
    return listBenchmarks(projectId)
      .then((runs) => {
        setRuns(runs);
        setError(null);
      })
      .catch((e) => setError(toError(e)));
  }, [projectId, toError]);

  useEffect(() => {
    load();
    listModels(projectId).then(setModels).catch(() => {});
    getProject(projectId).then(setProject).catch(() => {});
  }, [load, projectId]);

  // Polling only while a run is active (no useless loop).
  useEffect(() => {
    if (!hasActive) return;
    const id = setInterval(() => {
      void load();
    }, 5000);
    return () => clearInterval(id);
  }, [hasActive, load]);

  // spec clarte-3 R8: stop + delete confirm through the Modal (was window.confirm).
  const [confirm, setConfirm] = useState<{ kind: "stop" | "delete"; runId: string } | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  async function handleStop(runId: string) {
    setConfirm({ kind: "stop", runId });
  }
  async function handleDelete(runId: string) {
    setConfirm({ kind: "delete", runId });
  }
  async function confirmAction() {
    if (!confirm) return;
    setConfirmBusy(true);
    setError(null);
    try {
      if (confirm.kind === "stop") await cancelBenchmark(projectId, confirm.runId);
      else await deleteBenchmark(projectId, confirm.runId);
      setConfirm(null);
      await load();
    } catch (err) {
      setError(toError(err));
    } finally {
      setConfirmBusy(false);
    }
  }

  async function handleRerun(runId: string) {
    setError(null);
    try {
      await rerunBenchmark(projectId, runId);
      await load();
    } catch (err) {
      setError(toError(err));
    }
  }

  async function handleRetryErrors(runId: string) {
    setError(null);
    try {
      await retryBenchmarkErrors(projectId, runId);
      // Optimistic update: mark the run as PENDING so the polling kicks in
      // (the celery task changes the status in the DB a moment later).
      setRuns((prev) =>
        prev
          ? prev.map((r) => (r.id === runId ? { ...r, status: "PENDING" } : r))
          : prev
      );
      await load();
    } catch (err) {
      setError(toError(err));
    }
  }

  if (error && !runs) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!runs || !searches) return <Skeleton rows={3} />;
  // spec clarte-2 R3: no corpus → no benchmark can run; the empty state replaces the tool
  if (project?.article_count === 0) {
    return (
      <EmptyState
        title={tp("noCorpus")}
        action={
          isProjectLead(project, user) ? (
            <Link href={`/projects/${projectId}/import`}>
              <Button size="sm">{tp("importCta")}</Button>
            </Link>
          ) : undefined
        }
      />
    );
  }

  // Concordance is posed to a standard search (its analyses); the current one
  // wins by default, then the first standard. A stored choice that no longer
  // exists in the list (search deleted in the meantime) falls back instead of
  // leaving the panels pointed at a dead id.
  const standard = searches.filter((s) => s.kind === "standard");
  const concSearchId =
    concChoice && standard.some((s) => s.id === concChoice)
      ? concChoice
      : current && standard.some((s) => s.id === current.id)
        ? current.id
        : (standard[0]?.id ?? null);

  // Newest first.
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
              analysisSearchId={searchId}
              onStop={(id) => void handleStop(id)}
              onDelete={(id) => void handleDelete(id)}
              onRerun={(id) => void handleRerun(id)}
              onRetryErrors={(id) => void handleRetryErrors(id)}
              defaultOpen={run.id === runParam}
            />
          ))}
        </div>
      )}

      {/* Concordance (spec 2026-11-08): the analyses page is simplified — this
          page is the single home for the ad-hoc model comparison and the
          reviewer agreement, per search. */}
      {standard.length > 0 && (
        <section data-testid="bm-concordance" className="flex flex-col gap-3">
          <div>
            <h2 className="text-lg font-bold text-foreground">{t("concordance")}</h2>
            <p className="mt-1 text-sm text-muted">{t("concordanceIntro")}</p>
          </div>
          <div className="flex items-center gap-2">
            <label
              className="text-xs font-medium uppercase tracking-wide text-muted"
              htmlFor="bm-concordance-search"
            >
              {t("concordanceSearch")}
            </label>
            <select
              id="bm-concordance-search"
              data-testid="bm-concordance-search"
              value={concSearchId ?? ""}
              onChange={(e) => setConcChoice(e.target.value)}
              className="rounded-lg border border-border bg-surface px-2 py-1 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
            >
              {standard.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          {concSearchId && (
            <>
              <div data-testid="bm-comparison">
                <ComparisonPanel
                  key={`cmp-${concSearchId}`}
                  projectId={projectId}
                  searchId={concSearchId}
                />
              </div>
              <div data-testid="bm-agreement">
                <AgreementPanel
                  key={`agr-${concSearchId}`}
                  projectId={projectId}
                  searchId={concSearchId}
                  models={models}
                />
              </div>
            </>
          )}
        </section>
      )}
      {confirm && (
        <Modal title={confirm.kind === "stop" ? t("stop") : t("delete")} onClose={() => setConfirm(null)}>
          <div className="flex flex-col gap-4">
            <p className="text-sm text-muted">{confirm.kind === "stop" ? t("stopConfirm") : t("deleteConfirm")}</p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setConfirm(null)}>
                {tc("cancel")}
              </Button>
              <Button variant="danger" size="sm" loading={confirmBusy} onClick={() => void confirmAction()}>
                {confirm.kind === "stop" ? t("stop") : t("delete")}
              </Button>
            </div>
          </div>
        </Modal>
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
