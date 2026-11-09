"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  Watch,
  WatchCriterion,
  createWatch,
  deleteWatch,
  listWatches,
  runWatch,
  updateWatch,
} from "@/lib/api";
import { useSearchId, useSearches } from "@/lib/useSearch";
import { currentSearch } from "@/lib/search";
import { useApiError } from "@/lib/errorMsg";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { EmptyState, ErrorBanner, Skeleton } from "@/components/ui/Feedback";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/Field";
import CriteriaBuilder from "./CriteriaBuilder";

const SOURCES = [
  { key: "pubmed", labelKey: "source_pubmed" },
  { key: "europepmc", labelKey: "source_europepmc" },
  { key: "openalex", labelKey: "source_openalex" },
  { key: "hal", labelKey: "source_hal" },
];

/** A watch pass summary: found / imported / matched, with the last matching articles. */
function LastPass({ watch }: { watch: Watch }) {
  const t = useTranslations("watches");
  const lm = watch.last_matched;
  if (!lm) return <span className="text-muted">{t("never")}</span>;
  const found = typeof lm.found === "number" ? lm.found : 0;
  const imported = typeof lm.imported === "number" ? lm.imported : 0;
  const matched = typeof lm.matched === "number" ? lm.matched : 0;
  const details = lm.matched_details ?? [];
  return (
    <div className="flex flex-col gap-1 text-sm">
      <div className="text-muted">
        {found} {t("found")} · {imported} {t("imported")} ·{" "}
        <span className={matched > 0 ? "font-semibold text-primary" : "text-muted"}>
          {matched} {t("matches")}
        </span>
      </div>
      {matched === 0 && <div className="text-xs text-muted">{t("noMatch")}</div>}
      {details.length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {details.slice(0, 10).map((m) => (
            <li key={m.id} className="flex items-baseline gap-1.5 text-xs">
              <span className="min-w-0 flex-1 truncate text-foreground" title={m.title ?? undefined}>
                {m.title ?? m.id}
              </span>
              {m.pmid && <span className="text-muted">{m.pmid}</span>}
              {typeof m.confidence === "number" && (
                <span className="text-muted">{m.confidence.toFixed(2)}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function WatchCard({
  watch,
  onToggle,
  onRun,
  onDelete,
  busy,
}: {
  watch: Watch;
  onToggle: (id: string) => void;
  onRun: (id: string) => void;
  onDelete: (id: string) => void;
  busy: string | null;
}) {
  const t = useTranslations("watches");
  const active = watch.status === "ACTIVE";
  return (
    <div className="rounded-lg bg-surface shadow-sm p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-foreground">{watch.source_name}</span>
            <span
              className={`rounded-full px-2 py-0.5 text-xs ${
                active ? "bg-primary/10 text-primary" : "bg-surface-muted text-muted"
              }`}
            >
              {t(`status_${watch.status}`)}
            </span>
            {watch.notify_all && <span className="text-xs text-muted">{t("notifyAll")}</span>}
          </div>
          <p className="mt-1 line-clamp-2 text-sm text-muted" title={watch.query}>
            {watch.query}
          </p>
          {watch.search && (
            <p className="mt-0.5 text-xs text-muted">
              {t("search")}: {watch.search.name}
            </p>
          )}
          {(watch.criteria?.length ?? 0) > 0 ? (
            <p className="mt-0.5 text-xs text-muted">
              {watch.criteria!.length} {t("criteria")} · {watch.combine ?? "AND"}
            </p>
          ) : (
            watch.match_threshold != null && (
              <p className="mt-0.5 text-xs text-muted">
                {t("threshold")} ≥ {watch.match_threshold.toFixed(2)}
              </p>
            )
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            loading={busy === `run:${watch.id}`}
            onClick={() => onRun(watch.id)}
          >
            {t("runNow")}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            loading={busy === `pause:${watch.id}`}
            onClick={() => onToggle(watch.id)}
          >
            {active ? t("pause") : t("resume")}
          </Button>
          <Button size="sm" variant="danger" onClick={() => onDelete(watch.id)}>
            {t("delete")}
          </Button>
        </div>
      </div>
      <div className="mt-2 grid gap-3 md:grid-cols-2">
        <div>
          <div className="mb-0.5 text-xs font-medium text-muted">{t("lastRun")}</div>
          {watch.last_run_at ? (
            <span className="text-sm text-foreground">
              {new Date(watch.last_run_at).toLocaleString()}
            </span>
          ) : (
            <span className="text-sm text-muted">{t("never")}</span>
          )}
        </div>
        <div>
          <div className="mb-0.5 text-xs font-medium text-muted">{t("lastPass")}</div>
          <LastPass watch={watch} />
        </div>
      </div>
    </div>
  );
}

function WatchForm({
  projectId,
  defaultSearchId,
  onCreated,
  onCancel,
}: {
  projectId: string;
  defaultSearchId: string | undefined;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const t = useTranslations("watches");
  const toError = useApiError();
  const { searches } = useSearches(projectId);
  const [searchId, setSearchId] = useState(defaultSearchId ?? searches?.[0]?.id ?? "");
  const [source, setSource] = useState(SOURCES[0].key);
  const [query, setQuery] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [notifyAll, setNotifyAll] = useState(false);
  const [criteria, setCriteria] = useState<WatchCriterion[]>([]);
  const [combine, setCombine] = useState<"AND" | "OR">("AND");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const standard = (searches ?? []).filter((s) => s.kind === "standard");
  const options = standard.length > 0 ? standard : searches ?? [];
  // Eligible fields of the chosen search (the criteria builder offers boolean/choice).
  const fields = (searches ?? []).find((s) => s.id === searchId)?.fields ?? [];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!searchId || !query.trim()) {
      setError(t("requiredFields"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createWatch(projectId, {
        search_id: searchId,
        source,
        query: query.trim(),
        date_from: dateFrom || null,
        notify_all: notifyAll,
        criteria: criteria.length ? criteria : null,
        combine: criteria.length ? combine : null,
      });
      onCreated();
    } catch (err) {
      setError(toError(err));
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={(e) => void submit(e)}
      className="flex flex-col gap-3 rounded-lg bg-primary/5 p-3"
    >
      <div className="grid gap-3 md:grid-cols-2">
        <Field label={t("search")}>
          <Select
            value={searchId}
            onChange={(e) => {
              setSearchId(e.target.value);
              setCriteria([]); // criteria refer to the previous search's fields
            }}
          >
            {options.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t("source")}>
          <Select value={source} onChange={(e) => setSource(e.target.value)}>
            {SOURCES.map((s) => (
              <option key={s.key} value={s.key}>
                {t(s.labelKey)}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label={t("query")} hint={t("queryHint")}>
        <Textarea value={query} onChange={(e) => setQuery(e.target.value)} rows={2} required />
      </Field>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label={t("dateFrom")} hint={t("dateFromHint")}>
          <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </Field>
        <div className="flex items-end">
          <Checkbox label={t("notifyAll")} checked={notifyAll} onChange={(e) => setNotifyAll(e.target.checked)} />
        </div>
      </div>
      {fields.length > 0 && (
        <CriteriaBuilder
          fields={fields}
          criteria={criteria}
          combine={combine}
          onChange={(c, k) => {
            setCriteria(c);
            setCombine(k);
          }}
        />
      )}
      {error && (
        <div role="alert" className="flex items-center gap-1 text-sm text-danger">
          <span aria-hidden>⚠</span>
          {error}
        </div>
      )}
      <div className="flex items-center gap-2">
        <Button type="submit" loading={busy}>
          {t("create")}
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>
          {t("cancel")}
        </Button>
      </div>
    </form>
  );
}

function WatchesContent() {
  const t = useTranslations("watches");
  const toError = useApiError();
  const projectId = useParams<{ id: string }>().id;
  const searchId = useSearchId(projectId);
  const { searches } = useSearches(projectId, searchId);
  const [watches, setWatches] = useState<Watch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    return listWatches(projectId)
      .then((ws) => {
        setWatches(ws);
        setError(null);
      })
      .catch((e) => setError(toError(e)));
  }, [projectId, toError]);

  useEffect(() => {
    load();
  }, [load]);

  const defSearch = currentSearch(searches ?? [], searchId)?.id ?? searches?.[0]?.id;

  async function handleToggle(id: string) {
    const next = (watches ?? []).find((w) => w.id === id);
    if (!next) return;
    setBusy(`pause:${id}`);
    setError(null);
    try {
      await updateWatch(projectId, id, { status: next.status === "ACTIVE" ? "PAUSED" : "ACTIVE" });
      await load();
    } catch (err) {
      setError(toError(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleRun(id: string) {
    setBusy(`run:${id}`);
    setError(null);
    try {
      await runWatch(projectId, id);
      await load();
    } catch (err) {
      setError(toError(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm(t("deleteConfirm"))) return;
    setBusy(`pause:${id}`);
    setError(null);
    try {
      await deleteWatch(projectId, id);
      await load();
    } catch (err) {
      setError(toError(err));
    } finally {
      setBusy(null);
    }
  }

  if (error && !watches) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!watches || !searches) return <Skeleton rows={3} />;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
          <p className="mt-1 text-sm text-muted">{t("intro")}</p>
        </div>
        <Button onClick={() => setShowForm((v) => !v)}>{t("new")}</Button>
      </div>

      {error && <ErrorBanner message={error} />}

      {showForm && searches.length > 0 && (
        <WatchForm
          projectId={projectId}
          defaultSearchId={defSearch}
          onCreated={() => {
            setShowForm(false);
            void load();
          }}
          onCancel={() => setShowForm(false)}
        />
      )}

      {watches.length === 0 ? (
        <EmptyState title={t("empty")} hint={t("emptyHint")} />
      ) : (
        <div className="flex flex-col gap-3">
          {watches.map((w) => (
            <WatchCard
              key={w.id}
              watch={w}
              busy={busy}
              onToggle={(id) => void handleToggle(id)}
              onRun={(id) => void handleRun(id)}
              onDelete={(id) => void handleDelete(id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function WatchesPage() {
  return (
    <RequireAuth>
      <WatchesContent />
    </RequireAuth>
  );
}
