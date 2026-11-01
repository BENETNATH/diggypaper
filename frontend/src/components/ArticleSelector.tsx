"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { previewSelection, SearchInfo, Selection, PredictionFilter } from "@/lib/api";
import { useApiError } from "@/lib/errorMsg";
import { cleanSelection } from "@/lib/search";
import { AnswerFilters } from "@/components/AnswerFilters";
import { Button } from "@/components/ui/Button";

// NEEDS_HUMAN_REVIEW is not listed: TO_REVIEW already covers it (its i18n key is missing in all locales).
const STATUSES: PredictionFilter[] = ["AUTO_ACCEPTED", "AUTO_REJECTED", "TO_REVIEW", "NONE"];
const selectCls = "rounded border border-border bg-surface p-1 text-sm text-foreground";
const inputCls = `${selectCls} w-20`;

const tri = (v: boolean | undefined): string => (v === undefined ? "" : String(v));
const untri = (v: string): boolean | undefined => (v === "" ? undefined : v === "true");
const pct = (v: number | undefined): string => (v === undefined ? "" : String(Math.round(v * 100)));
const unpct = (v: string): number | undefined => {
  const n = parseFloat(v);
  return Number.isNaN(n) ? undefined : Math.min(100, Math.max(0, n)) / 100;
};

/**
 * Choosing the articles of a search: we start from the batch (and results) of an existing search,
 * we filter (status, answers, re-review, full text, confidence), optionally we draw
 * N articles at random. The criteria are cumulative; live preview of the article count.
 */
export function ArticleSelector({
  projectId,
  searches,
  value,
  onChange,
  onCount,
}: {
  projectId: string;
  searches: SearchInfo[];
  value: Selection;
  onChange: (next: Selection) => void;
  /** Number of selected articles (null: computing or unavailable). */
  onCount?: (n: number | null) => void;
}) {
  const t = useTranslations("searches");
  const ta = useTranslations("articles");
  const errText = useApiError(); // raw API error → i18n message
  const initial = searches.find((s) => s.is_default) ?? searches[0];
  const origin = searches.find((s) => s.id === value.from_search_id) ?? initial;
  const [count, setCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const set = (patch: Partial<Selection>) => onChange({ ...value, ...patch });
  // Criteria filter analysis results: meaningless before the first analysis.
  const hasPredictions = searches.some((s) => s.predicted > 0);
  const answers = useMemo(
    () => Object.fromEntries(Object.entries(value.answers ?? {}).map(([k, v]) => [k, String(v)])),
    [value.answers]
  );

  // Live preview (debounced by 400 ms to avoid calling the server on every keystroke).
  const key = JSON.stringify(cleanSelection(value));
  useEffect(() => {
    let stale = false;
    const timer = setTimeout(() => {
      setCount(null);
      setError(null);
      onCount?.(null);
      previewSelection(projectId, JSON.parse(key) as Selection)
        .then((n) => {
          if (stale) return;
          setCount(n);
          onCount?.(n);
        })
        .catch((e) => {
          if (!stale) setError(errText(e));
        });
    }, 400);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
    // onCount is deliberately outside the dependencies (parent callback, unstable identity)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, key]);

  return (
    <div className="flex flex-col gap-3">
      <label className="flex flex-col gap-1 text-sm text-foreground">
        {t("selFrom")}
        <select
          value={origin?.id ?? ""}
          onChange={(e) => {
            // The items differ from one search to the next: we restart without answer filters.
            const id = e.target.value;
            set({
              from_search_id: id === initial?.id ? undefined : id,
              answers: undefined,
            });
          }}
          className={`${selectCls} max-w-md`}
        >
          {searches.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} — {s.lot_size} {t("articlesUnit")}
            </option>
          ))}
        </select>
        {hasPredictions && <span className="text-xs text-muted">{t("selFromHint")}</span>}
      </label>

      {hasPredictions && (
        <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-surface p-3">
        <label className="flex flex-col gap-0.5 text-xs text-muted">
          {t("selStatus")}
          <select
            value={value.status ?? ""}
            onChange={(e) => set({ status: (e.target.value || undefined) as PredictionFilter | undefined })}
            className={selectCls}
          >
            <option value="">{t("any")}</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {ta(`filter_${s}`)}
              </option>
            ))}
          </select>
        </label>
        <AnswerFilters
          fields={origin?.fields ?? []}
          values={answers}
          onChange={(k, v) => set({ answers: { ...(value.answers ?? {}), [k]: v } })}
        />
        <label className="flex flex-col gap-0.5 text-xs text-muted">
          {t("selReviewed")}
          <select
            value={tri(value.reviewed)}
            onChange={(e) => set({ reviewed: untri(e.target.value) })}
            className={selectCls}
          >
            <option value="">{t("any")}</option>
            <option value="true">{t("reviewedYes")}</option>
            <option value="false">{t("reviewedNo")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5 text-xs text-muted">
          {t("selFulltext")}
          <select
            value={tri(value.has_fulltext)}
            onChange={(e) => set({ has_fulltext: untri(e.target.value) })}
            className={selectCls}
          >
            <option value="">{t("any")}</option>
            <option value="true">{t("fulltextYes")}</option>
            <option value="false">{t("fulltextNo")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5 text-xs text-muted">
          {t("selConfMin")}
          <input
            type="number"
            min={0}
            max={100}
            value={pct(value.confidence_min)}
            onChange={(e) => set({ confidence_min: unpct(e.target.value) })}
            className={inputCls}
          />
        </label>
        <label className="flex flex-col gap-0.5 text-xs text-muted">
          {t("selConfMax")}
          <input
            type="number"
            min={0}
            max={100}
            value={pct(value.confidence_max)}
            onChange={(e) => set({ confidence_max: unpct(e.target.value) })}
            className={inputCls}
          />
        </label>
        </div>
      )}

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-surface p-3">
        <label className="flex flex-col gap-0.5 text-xs text-muted">
          {t("selSample")}
          <input
            type="number"
            min={1}
            value={value.sample_n ?? ""}
            placeholder={t("selSampleAll")}
            onChange={(e) => {
              const n = parseInt(e.target.value, 10);
              // Seed drawn as soon as the draw is enabled: the preview and creation see the same sample.
              set(
                n > 0
                  ? { sample_n: n, seed: value.seed ?? Math.floor(Math.random() * 1e9) }
                  : { sample_n: undefined, seed: undefined }
              );
            }}
            className="w-28 rounded border border-border bg-surface p-1 text-sm text-foreground"
          />
        </label>
        {value.sample_n ? (
          <>
            <label className="flex flex-col gap-0.5 text-xs text-muted">
              {t("selSeed")}
              <input
                type="number"
                value={value.seed ?? ""}
                onChange={(e) => set({ seed: e.target.value === "" ? undefined : parseInt(e.target.value, 10) })}
                className="w-32 rounded border border-border bg-surface p-1 text-sm text-foreground"
              />
            </label>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => set({ seed: Math.floor(Math.random() * 1e9) })}
            >
              {t("selNewDraw")}
            </Button>
          </>
        ) : null}
        <span className="basis-full text-xs text-muted">{t("selSampleHint")}</span>
      </div>

      <p className="text-sm text-foreground" role="status">
        {error ? (
          <span className="text-danger">{error}</span>
        ) : count === null ? (
          <span className="text-muted">{t("counting")}</span>
        ) : (
          <strong>{t("selected", { n: count })}</strong>
        )}
      </p>
    </div>
  );
}
