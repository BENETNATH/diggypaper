"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { CRITERIA_STATUSES, Criteria, SearchInfo, criteriaCount } from "@/lib/api";

const selectCls =
  "rounded border border-border bg-surface p-1 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus";
const inputCls = `${selectCls} w-20`;

const STATUS_KEY: Record<string, string> = {
  AUTO_ACCEPTED: "critStatusAutoAccepted",
  AUTO_REJECTED: "critStatusAutoRejected",
  NEEDS_HUMAN_REVIEW: "critStatusReview",
  ERROR: "critStatusError",
};

/** True when at least one criteria family is set (answer / confidence / status). */
export function criteriaActive(c: Criteria): boolean {
  return Boolean(c.response || c.confidence || c.status?.length);
}

const conf = (v: number | undefined): string => (v === undefined ? "" : String(Math.round(v * 100) / 100));
const unconf = (v: string): number | undefined => {
  const n = parseFloat(v);
  return Number.isNaN(n) ? undefined : Math.min(1, Math.max(0, n));
};

/**
 * « Critères sur les résultats » (spec 2026-11-09-sous-analyse-criteres §4):
 * AND of 3 families (answer / confidence / status) over the source analysis's
 * results. The live counter is the server's (300 ms debounce); 0 kept → the
 * launch button is disabled by the parent (onCount). The chain label is
 * previewed with the existing « issue de » vocabulary.
 */
export function CriteriaBlock({
  projectId,
  search,
  value,
  onChange,
  newName,
  onCount,
}: {
  projectId: string;
  /** The source analysis (A): its fields drive the possible criteria. */
  search: SearchInfo;
  value: Criteria;
  onChange: (next: Criteria) => void;
  /** Name of the analysis being created ("B"); empty = the « B » placeholder. */
  newName: string;
  /** Number of kept articles (null: computing). */
  onCount?: (n: number | null) => void;
}) {
  const t = useTranslations("inference");
  const tP = useTranslations("project");
  // The live counter is only valid for the exact (search, criteria) it was
  // computed for — any other key reads « calculating » (no synchronous
  // state reset inside the effect).
  const [countState, setCountState] = useState<{ s: string; key: string; n: number } | null>(null);

  // Live counter (300 ms debounce — the spec's figure, not the selector's 400).
  // An empty status list is dropped: no criterion = the whole lot.
  const norm: Criteria = { ...value };
  if (!norm.status?.length) delete norm.status;
  const key = JSON.stringify(norm);
  const count = countState && countState.s === search.id && countState.key === key ? countState.n : null;
  useEffect(() => {
    let stale = false;
    onCount?.(null); // « calculating » for the new key (the parent's state)
    const timer = setTimeout(() => {
      criteriaCount(projectId, search.id, JSON.parse(key) as Criteria)
        .then((n) => {
          if (stale) return;
          setCountState({ s: search.id, key, n });
          onCount?.(n);
        })
        .catch(() => {
          if (!stale) setCountState(null); // server error: the counter stays « calculating », not 0
        });
    }, 300);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
    // onCount is deliberately outside the dependencies (parent callback, unstable identity)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, search.id, key]);

  const field = value.response?.field ?? "";
  const op = value.response?.op ?? "=";
  const fv = value.response?.value ?? "";
  const fdef = search.fields.find((f) => f.key === field);
  // The field type drives the operators and the values (R1): boolean → Oui/Non,
  // choice → its options, text → free value (contains).
  const ops = fdef?.type === "text" ? (["=", "!=", "contains"] as const) : (["=", "!="] as const);

  function setResponse(patch: Partial<NonNullable<Criteria["response"]>> | { field?: string }) {
    if ("field" in patch && !patch.field) {
      // « Aucun »: the family is dropped entirely — a response object left
      // without field would serialize as {op, value} and 422 on the server
      // (the counter would then stay « calculating » forever).
      onChange({ ...value, response: undefined });
      return;
    }
    if (patch.field !== undefined && patch.field !== field) {
      // New field: restart its operator and value.
      const f = search.fields.find((x) => x.key === patch.field);
      onChange({
        ...value,
        response: patch.field
          ? { field: patch.field, op: "=", value: f?.type === "boolean" ? "true" : f?.type === "choice" ? f.options?.[0] ?? "" : "" }
          : undefined,
      });
      return;
    }
    onChange({ ...value, response: field ? { field, op, value: fv, ...patch } : undefined });
  }

  const k = (value.response ? 1 : 0) + (value.confidence ? 1 : 0) + (value.status?.length ? 1 : 0);

  return (
    <div data-testid="criteria-block" className="rounded-lg bg-surface-muted p-3">
      <p className="text-sm font-medium text-foreground">{t("critTitle")}</p>
      <p className="mt-1 text-xs text-muted">{t("critHint", { n: search.predicted })}</p>

      <div className="mt-2.5 flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="w-16 text-xs text-muted">{t("critResponse")}</span>
          <select
            aria-label={t("critResponse")}
            value={field}
            onChange={(e) => setResponse({ field: e.target.value || undefined })}
            className={selectCls}
          >
            <option value="">{t("critNone")}</option>
            {search.fields.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
          {fdef && (
            <select
              aria-label={t("critOpEq")}
              value={op}
              onChange={(e) => setResponse({ op: e.target.value as "=" | "!=" | "contains" })}
              className={selectCls}
            >
              {ops.map((o) => (
                <option key={o} value={o}>
                  {o === "=" ? t("critOpEq") : o === "!=" ? t("critOpNe") : t("critOpContains")}
                </option>
              ))}
            </select>
          )}
          {fdef &&
            (fdef.type === "text" ? (
              <input
                aria-label={t("critResponse")}
                type="search"
                value={fv}
                onChange={(e) => setResponse({ value: e.target.value })}
                className={`${inputCls} flex-1`}
              />
            ) : (
              <select
                aria-label={t("critResponse")}
                value={fv}
                onChange={(e) => setResponse({ value: e.target.value })}
                className={selectCls}
              >
                {fdef.type === "boolean" ? (
                  <>
                    <option value="true">{t("critYes")}</option>
                    <option value="false">{t("critNo")}</option>
                  </>
                ) : (
                  (fdef.options ?? []).map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))
                )}
              </select>
            ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="w-16 text-xs text-muted">{t("critConfidence")}</span>
          <input
            aria-label={t("critConfidence")}
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={conf(value.confidence?.[0])}
            onChange={(e) => {
              const min = unconf(e.target.value);
              onChange({
                ...value,
                confidence:
                  min === undefined && value.confidence?.[1] === undefined
                    ? undefined
                    : [min ?? value.confidence?.[0] ?? 0, value.confidence?.[1] ?? 1],
              });
            }}
            className={inputCls}
          />
          <span className="text-muted">—</span>
          <input
            aria-label={t("critConfidence")}
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={conf(value.confidence?.[1])}
            onChange={(e) => {
              const max = unconf(e.target.value);
              onChange({
                ...value,
                confidence:
                  max === undefined && value.confidence?.[0] === undefined
                    ? undefined
                    : [value.confidence?.[0] ?? 0, max ?? value.confidence?.[1] ?? 1],
              });
            }}
            className={inputCls}
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="w-16 text-xs text-muted">{t("critStatus")}</span>
          {CRITERIA_STATUSES.map((s) => {
            const on = value.status?.includes(s) ?? false;
            return (
              <button
                key={s}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  onChange({
                    ...value,
                    status: on ? (value.status ?? []).filter((x) => x !== s) : [...(value.status ?? []), s],
                  })
                }
                className={`rounded-full px-3 py-1 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus ${
                  on ? "bg-primary text-primary-foreground" : "bg-surface text-muted"
                }`}
              >
                {t(STATUS_KEY[s])}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mt-3 border-t border-border pt-2">
        <p
          data-testid="crit-count"
          role="status"
          className={`text-sm font-semibold ${count === 0 ? "text-danger" : "text-success"}`}
        >
          {count === null ? t("critCounting") : t("critCount", { n: count })}
        </p>
        {count === 0 && <p className="mt-1 text-xs text-danger">{t("critZero")}</p>}
        {k > 0 && (
          <p className="mt-2 rounded-lg bg-surface p-2 text-xs text-muted">
            {t("critChainIntro")}{" "}
            <b className="font-semibold text-foreground">
              « {(newName.trim() || "B")} {tP("derivedFrom", { name: search.name })} · {t("critChainK", { k })} ({count ?? "…"}) »
            </b>
          </p>
        )}
      </div>
    </div>
  );
}
