"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import {
  createSearch,
  deleteSearch,
  getProject,
  listModels,
  ModelInfo,
  renameSearch,
  SchemaField,
  SearchInfo,
  SearchKind,
  Selection,
} from "@/lib/api";
import { SearchCreate } from "@/lib/api";
import { useApiError } from "@/lib/errorMsg";
import { useHasMounted } from "@/lib/useHasMounted";
import { cleanSelection, isUntouched, prefillStoreKey, withSearch } from "@/lib/search";
import { notifySearchesChanged, storeSearchId, useSearchId, useSearches, useSelectSearch } from "@/lib/useSearch";
import { ModelRef } from "@/components/ModelRef";
import { ArticleSelector } from "@/components/ArticleSelector";
import { QuestionEditor } from "@/components/QuestionEditor";
import { useAuth } from "@/lib/auth";
import { isProjectLead } from "@/lib/role";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Badge, Card, statusColor } from "@/components/ui/Card";
import { EmptyState, ErrorBanner, Skeleton } from "@/components/ui/Feedback";

const DEFAULT_BLIND_N = 40;

type Definition = { question: string; fields: SchemaField[]; sections: string[] };

/** Items cleaned for the API (choice options without empty entries). */
const cleanFields = (fields: SchemaField[]): SchemaField[] =>
  fields.map((f) => ({ ...f, options: f.type === "choice" ? (f.options ?? []).filter(Boolean) : undefined }));

function SearchCard({
  s,
  current,
  isLead,
  onOpen,
  onChanged,
  onDeleted,
}: {
  s: SearchInfo;
  current: boolean;
  isLead: boolean;
  onOpen: () => void;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const t = useTranslations("searches");
  const tj = useTranslations("jobs");
  const errText = useApiError(); // raw API error → i18n message
  const locale = useLocale();
  const projectId = useParams<{ id: string }>().id;
  const [renaming, setRenaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(fn: () => Promise<unknown>, after: () => void) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      after();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={`rounded-xl border p-4 ${current ? "border-primary bg-primary/5" : "border-border bg-surface"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {renaming === null ? (
            <h3 className="flex flex-wrap items-center gap-2 font-semibold text-foreground">
              {s.name}
              {s.is_default && <Badge color="blue">{t("initial")}</Badge>}
              {s.kind !== "standard" && (
                <span title={t(`kindHint_${s.kind}`)}>
                  <Badge color={s.kind === "blind" ? "yellow" : "gray"}>{t(`kind_${s.kind}`)}</Badge>
                </span>
              )}
              {current && <Badge color="green">{t("current")}</Badge>}
              {s.schedule_state === "waiting" && <Badge color="gray">{t("scheduledBadge")}</Badge>}
              {s.schedule_state === "failed" && <Badge color="red">{t("scheduleFailedBadge")}</Badge>}
            </h3>
          ) : (
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (renaming.trim())
                  void run(() => renameSearch(projectId, s.id, renaming.trim()), () => {
                    setRenaming(null);
                    onChanged();
                  });
              }}
            >
              <Input value={renaming} onChange={(e) => setRenaming(e.target.value)} aria-label={t("rename")} autoFocus />
              <Button type="submit" size="sm" loading={busy} disabled={!renaming.trim()}>
                {t("save")}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setRenaming(null)}>
                {t("cancel")}
              </Button>
            </form>
          )}
          {s.parent && (
            <p className="mt-0.5 text-xs text-muted">
              {t("derivedFrom")} : <span className="text-foreground">{s.parent.name}</span>
            </p>
          )}
          <p className="mt-1 text-sm text-foreground/80">{s.question}</p>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <span>{t("stats", { lot: s.lot_size, predicted: s.predicted, reviewed: s.reviewed })}</span>
            {s.last_job && (
              <span className="inline-flex items-center gap-1">
                {t("lastJob")} <Badge color={statusColor(s.last_job.status)}>{tj(`status_${s.last_job.status}`)}</Badge>
                <code>
                  <ModelRef name={s.last_job.model} version={s.last_job.model_version} />
                </code>
              </span>
            )}
            {s.created_by && (
              <span>
                {s.created_by}
                {s.created_at ? ` · ${new Date(s.created_at).toLocaleDateString(locale)}` : ""}
              </span>
            )}
          </p>
          {error && <p className="mt-1 text-xs text-danger">{error}</p>}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button size="sm" onClick={onOpen}>
            {t("open")}
          </Button>
          {isLead && renaming === null && (
            <Button size="sm" variant="secondary" onClick={() => setRenaming(s.name)}>
              {t("rename")}
            </Button>
          )}
          {isLead && !s.is_default && (
            <Button
              size="sm"
              variant="danger"
              loading={busy}
              onClick={() => {
                if (window.confirm(t("deleteConfirm", { name: s.name })))
                  void run(() => deleteSearch(projectId, s.id), onDeleted);
              }}
            >
              {t("delete")}
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}

function SearchesContent() {
  const t = useTranslations("searches");
  const errText = useApiError(); // raw API error → i18n message
  const projectId = useParams<{ id: string }>().id;
  const router = useRouter();
  const searchId = useSearchId(projectId);
  const { searches, current, reload } = useSearches(projectId, searchId);
  const selectSearch = useSelectSearch(projectId);
  const { user } = useAuth();
  const role = user?.role;
  const [isLead, setIsLead] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // "New search" form (possibly prefilled from the Articles page,
  // or opened by default via ?new=1 from the "New search" button of the overview).
  const [showForm, setShowForm] = useState(false);
  const openNew = useSearchParams()?.get("new") === "1";
  // "New search" button of the overview arrives with ?new=1: open the form.
  if (openNew && !showForm) setShowForm(true);
  const [name, setName] = useState("");
  const [selection, setSelection] = useState<Selection>({});
  const [count, setCount] = useState<number | null>(null);
  const [edit, setEdit] = useState<Definition | null>(null); // null: question copied as-is
  const [originDef, setOriginDef] = useState<Definition | null>(null);
  const [creating, setCreating] = useState(false);

  // Scheduled analysis: it starts when the source search finishes.
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [sched, setSched] = useState(false);
  const [m1, setM1] = useState("");
  const [m2, setM2] = useState("");
  const [instr, setInstr] = useState("");
  useEffect(() => {
    listModels(projectId).then(setModels).catch(() => setModels([]));
  }, [projectId]);

  // Blind sample
  const [blindN, setBlindN] = useState(DEFAULT_BLIND_N);
  const [blindBusy, setBlindBusy] = useState(false);

  useEffect(() => {
    getProject(projectId)
      .then((p) => setIsLead(isProjectLead(p, { role })))
      .catch(() => {});
  }, [projectId, role]);

  // One-shot prefill pushed by the Articles page (client only: sessionStorage).
  const mounted = useHasMounted();
  const [prefillApplied, setPrefillApplied] = useState(false);
  if (mounted && !prefillApplied) {
    setPrefillApplied(true);
    try {
      const raw = sessionStorage.getItem(prefillStoreKey(projectId));
      if (raw) {
        sessionStorage.removeItem(prefillStoreKey(projectId));
        setSelection(JSON.parse(raw) as Selection);
        setShowForm(true);
      }
    } catch {
      /* no prefill */
    }
  }

  // Question and items of the source search: copied by default, editable.
  const originId = selection.from_search_id ?? searches?.find((s) => s.is_default)?.id;
  // A new origin search: the definition is copied as-is again (edit reset to null).
  const originKey = `${projectId}:${originId ?? ""}:${showForm ? "1" : "0"}`;
  const [prevOriginKey, setPrevOriginKey] = useState(originKey);
  if (prevOriginKey !== originKey && originId && showForm) {
    setPrevOriginKey(originKey);
    setEdit(null);
  }
  useEffect(() => {
    if (!originId || !showForm) return;
    getProject(projectId, originId)
      .then((p) => {
        const set = p.question_set !== false; // sinon : formulaire vide + suggestion
        setOriginDef({
          question: set ? (p.question ?? "") : "",
          fields: set ? (p.fields ?? []) : [],
          sections: p.text_sections ?? ["methods"],
        });
      })
      .catch(() => setOriginDef(null));
  }, [projectId, originId, showForm]);

  function open(s: SearchInfo) {
    storeSearchId(projectId, s.id);
    router.push(withSearch(`/projects/${projectId}/articles`, s.id));
  }

  async function create(
    kind: SearchKind,
    body: { name?: string; selection: Selection; def?: Definition | null; schedule?: SearchCreate["schedule"] }
  ) {
    const s = await createSearch(projectId, {
      name: body.name,
      kind,
      ...(body.schedule ? { schedule: body.schedule } : {}),
      selection: cleanSelection(body.selection),
      ...(body.def
        ? {
            question: body.def.question,
            fields: cleanFields(body.def.fields),
            text_sections: body.def.sections,
          }
        : {}),
    });
    storeSearchId(projectId, s.id);
    notifySearchesChanged();
    return s;
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const model = m2 || m1;
      const s = await create("standard", {
        name: name.trim() || undefined,
        selection,
        def: edit,
        ...(sched && model
          ? {
              schedule: {
                model,
                ...(m2 && m1 && m1 !== m2 ? { prefilter_model: m1 } : {}),
                ...(instr.trim() ? { instructions: instr.trim() } : {}),
              },
            }
          : {}),
      });
      router.push(sched ? `/projects/${projectId}` : withSearch(`/projects/${projectId}/inference`, s.id));
    } catch (err) {
      setError(errText(err));
      setCreating(false);
    }
  }

  async function handleBlind() {
    setBlindBusy(true);
    setError(null);
    try {
      const from = current && !current.is_default ? current.id : undefined;
      const s = await create("blind", {
        selection: { from_search_id: from, sample_n: blindN, seed: Math.floor(Math.random() * 1e9) },
      });
      router.push(withSearch(`/projects/${projectId}/annotate?queue=all`, s.id));
    } catch (err) {
      setError(errText(err));
      setBlindBusy(false);
    }
  }

  // No real search: we hide "New search" (the analysis creates it); the blind sample stays (reference before analysis).
  const none = !!searches && searches.every(isUntouched);

  if (!searches) return error ? <ErrorBanner message={error} onRetry={() => void reload()} /> : <Skeleton rows={3} />;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("intro")}</p>
      </div>

      {error && <ErrorBanner message={error} />}

      {isLead && !none && !showForm && (
        <Button variant="secondary" onClick={() => setShowForm(true)}>
          + {t("newTitle")}
        </Button>
      )}
      {isLead && !none && showForm && (
        <Card title={t("newTitle")}>
          <form onSubmit={handleCreate} className="flex flex-col gap-4">
            <Field label={t("nameLabel")}>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("namePlaceholder")} />
            </Field>
            <ArticleSelector
              projectId={projectId}
              searches={searches}
              value={selection}
              onChange={setSelection}
              onCount={setCount}
            />
            <details className="rounded-lg border border-border p-3">
              <summary className="cursor-pointer text-sm font-medium text-foreground">{t("questionTitle")}</summary>
              <p className="my-2 text-xs text-muted">{t("questionHint")}</p>
              {edit ?? originDef ? (
                <QuestionEditor {...(edit ?? originDef)!} onChange={setEdit} />
              ) : (
                <p className="text-sm text-muted">…</p>
              )}
            </details>
            {models.length > 0 && (
              <div className="rounded-lg border border-border p-3">
                <label className="flex items-center gap-2 text-sm font-medium text-foreground">
                  <input type="checkbox" checked={sched} onChange={(e) => setSched(e.target.checked)} />
                  {t("scheduleLabel")}
                </label>
                <p className="mt-1 text-xs text-muted">
                  {t("scheduleHint", { origin: (searches?.find((s) => s.id === (selection.from_search_id ?? searches.find((x) => x.is_default)?.id)) ?? current)?.name ?? "…" })}
                </p>
                {sched && (
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <Field label={t("scheduleStage2")}>
                      <select value={m2} onChange={(e) => setM2(e.target.value)} className="w-full rounded border border-border bg-surface p-2 text-sm text-foreground">
                        <option value="">—</option>
                        {models.map((m) => (
                          <option key={m.id} value={m.name}>
                            {m.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label={t("scheduleStage1")}>
                      <select value={m1} onChange={(e) => setM1(e.target.value)} className="w-full rounded border border-border bg-surface p-2 text-sm text-foreground">
                        <option value="">—</option>
                        {models.map((m) => (
                          <option key={m.id} value={m.name}>
                            {m.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <div className="sm:col-span-2">
                      <Field label={t("scheduleInstructions")}>
                        <Input value={instr} onChange={(e) => setInstr(e.target.value)} maxLength={2000} />
                      </Field>
                    </div>
                  </div>
                )}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="submit"
                loading={creating}
                disabled={(sched ? !(m2 || m1) : !count) || (edit?.sections.length === 0)}
              >
                {sched ? t("createScheduled") : t("create")}
              </Button>
              <Button type="button" variant="secondary" onClick={() => setShowForm(false)}>
                {t("cancel")}
              </Button>
              {!sched && count === 0 && <span className="text-sm text-warning">{t("emptySelection")}</span>}
              {!sched && count === null && <span className="text-sm text-muted">{t("counting")}</span>}
            </div>
          </form>
        </Card>
      )}

      {none ? (
        <EmptyState
          title={t("empty")}
          hint={t("emptyHint")}
          action={
            isLead && (
              <Link href={`/projects/${projectId}/inference`}>
                <Button>{t("runAnalysis")} →</Button>
              </Link>
            )
          }
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {searches.filter((s) => !isUntouched(s)).map((s) => (
            <SearchCard
              key={s.id}
              s={s}
              current={s.id === current?.id}
              isLead={isLead}
              onOpen={() => open(s)}
              onChanged={() => {
                void reload();
                notifySearchesChanged();
              }}
              onDeleted={() => {
                // The deleted search may have been the current one: back to the initial search.
                const initial = searches.find((x) => x.is_default);
                if (s.id === current?.id && initial) selectSearch(initial.id);
                void reload();
                notifySearchesChanged();
              }}
            />
          ))}
        </ul>
      )}

      {isLead ? (
        <Card title={t("blindTitle")}>
          <p className="text-sm text-muted">{t("blindIntro")}</p>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <Field label={t("blindN")}>
              <Input
                type="number"
                min={1}
                value={blindN}
                onChange={(e) => setBlindN(Math.max(1, parseInt(e.target.value, 10) || 1))}
                className="w-28"
              />
            </Field>
            <Button loading={blindBusy} onClick={() => void handleBlind()}>
              {t("blindCreate")}
            </Button>
          </div>
        </Card>
      ) : (
        <p className="text-sm text-muted">{t("leadOnly")}</p>
      )}
    </div>
  );
}

export default function SearchesPage() {
  return (
    <RequireAuth>
      <SearchesContent />
    </RequireAuth>
  );
}
