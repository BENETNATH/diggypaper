"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { expandByCitations, listSearches, CitationPreview, SearchInfo, Selection } from "@/lib/api";
import { cleanSelection } from "@/lib/search";
import { useApiError } from "@/lib/errorMsg";
import { ArticleSelector } from "@/components/ArticleSelector";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Field";
import { EmptyState, ErrorBanner, Skeleton } from "@/components/ui/Feedback";

type Direction = "backward" | "forward" | "both";

/** Enrich the corpus by citations: references / citing articles of the chosen ones, without prior analysis. */
export function CitationExpand({ projectId, onStarted }: { projectId: string; onStarted: () => void }) {
  const t = useTranslations("import");
  const toError = useApiError();
  const [searches, setSearches] = useState<SearchInfo[] | null>(null);
  const [selection, setSelection] = useState<Selection>({});
  const [direction, setDirection] = useState<Direction>("both");
  const [overlap, setOverlap] = useState(1);
  const [maxCand, setMaxCand] = useState(300);
  const [preview, setPreview] = useState<CitationPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listSearches(projectId).then(setSearches).catch(() => setSearches([]));
  }, [projectId]);

  const body = () => ({ selection: cleanSelection(selection), direction, min_overlap: overlap, max_candidates: maxCand });
  const reset = () => setPreview(null);

  async function estimate() {
    setBusy(true);
    setError(null);
    try {
      setPreview(await expandByCitations(projectId, body(), true));
    } catch (e) {
      setError(toError(e));
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    setBusy(true);
    setError(null);
    try {
      await expandByCitations(projectId, body(), false);
      setPreview(null);
      onStarted();
    } catch (e) {
      setError(toError(e));
    } finally {
      setBusy(false);
    }
  }

  if (!searches) return <Skeleton rows={2} height="h-8" />;
  if (searches.length === 0)
    return (
      <EmptyState
        title={t("citNoSearchesTitle")}
        hint={t("citNoSearchesHint")}
        action={
          <Link
            href={`/projects/${projectId}/searches`}
            className="text-sm font-medium text-primary hover:underline"
          >
            {t("citNoSearchesLink")} →
          </Link>
        }
      />
    );
  const atLeast = preview?.by_overlap[String(overlap)];
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">{t("citIntro")}</p>
      {error && <ErrorBanner message={error} />}
      <div>
        <p className="mb-1 text-sm font-medium text-foreground">{t("citSeeds")}</p>
        <ArticleSelector
          projectId={projectId}
          searches={searches}
          value={selection}
          onChange={(s) => {
            setSelection(s);
            reset();
          }}
        />
      </div>
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">
          <span className="font-medium text-foreground">{t("citDirection")}</span>
          <Select
            value={direction}
            onChange={(e) => {
              setDirection(e.target.value as Direction);
              reset();
            }}
          >
            <option value="both">{t("citBoth")}</option>
            <option value="backward">{t("citBackward")}</option>
            <option value="forward">{t("citForward")}</option>
          </Select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium text-foreground">{t("citMinOverlap")}</span>
          <Select value={String(overlap)} onChange={(e) => setOverlap(Number(e.target.value))}>
            {[1, 2, 3, 5].map((n) => (
              <option key={n} value={n}>
                ≥ {n}
                {preview ? ` (${preview.by_overlap[String(n)] ?? 0})` : ""}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium text-foreground">{t("citMax")}</span>
          <input
            type="number"
            min={1}
            max={5000}
            value={maxCand}
            onChange={(e) => setMaxCand(Math.max(1, Number(e.target.value) || 1))}
            className="w-24 rounded border border-border bg-surface p-1.5 text-sm text-foreground"
          />
        </label>
      </div>
      {preview && (
        <p className="rounded-lg bg-surface-muted p-3 text-sm text-foreground">
          {t("citFound", { seeds: preview.seeds, candidates: preview.candidates })}{" "}
          <strong>{t("citAtLeast", { n: overlap, count: Math.min(atLeast ?? 0, maxCand) })}</strong>
        </p>
      )}
      {preview && (atLeast ?? 0) === 0 && <p className="text-sm text-warning">{t("citNoCandidate")}</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="secondary" loading={busy && !preview} disabled={busy} onClick={estimate}>
          {t("citEstimate")}
        </Button>
        <Button type="button" loading={busy && !!preview} disabled={!preview || busy || (atLeast ?? 0) === 0} onClick={run}>
          {t("citRun")}
        </Button>
      </div>
      <p className="text-xs text-muted">{t("citHint")}</p>
    </div>
  );
}
