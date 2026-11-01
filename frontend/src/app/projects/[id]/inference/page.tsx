"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { isProjectLead } from "@/lib/role";
import { ModelRef } from "@/components/ModelRef";
import {
  createSearch,
  cancelInference,
  getInference,
  getProject,
  getPrompt,
  listGold,
  listModels,
  triggerInference,
  InferenceJob,
  ModelInfo,
  SchemaField,
  SearchInfo,
  Validation,
  Selection,
} from "@/lib/api";
import { useApiError } from "@/lib/errorMsg";
import { MAX_GOLD_EXAMPLES } from "@/lib/benchmark";
import { ExportMenu } from "@/components/ExportMenu";
import { RequireAuth } from "@/components/RequireAuth";
import { QuestionEditor } from "@/components/QuestionEditor";
import { ArticleSelector } from "@/components/ArticleSelector";
import { Button } from "@/components/ui/Button";
import { Checkbox, Field, Input, Select } from "@/components/ui/Field";
import { ValidationView } from "@/components/ValidationView";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { EmptyState, ErrorBanner, Skeleton } from "@/components/ui/Feedback";
import { JobProgress } from "@/components/JobProgress";
import { cleanSelection, isUntouched, withSearch } from "@/lib/search";
import {
  notifySearchesChanged,
  storeSearchId,
  useSearchId,
  useSearches,
  useSelectSearch,
} from "@/lib/useSearch";

const num = (v: unknown): number => (typeof v === "number" ? v : 0);
const cleanFields = (fields: SchemaField[]): SchemaField[] =>
  fields.map((f) => ({ ...f, options: f.type === "choice" ? (f.options ?? []).filter(Boolean) : undefined }));

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className={`rounded-lg p-2 text-center ${tone ?? "bg-surface-muted"}`}>
      <div className="text-lg font-bold text-foreground">{value.toLocaleString()}</div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  );
}

/** One row of the "analyses performed" list: the question, its state and a link to its results. */
function AnalysisRow({ s, projectId, locale }: { s: SearchInfo; projectId: string; locale: string }) {
  const t = useTranslations("searches");
  const tj = useTranslations("jobs");
  const tp = useTranslations("project");
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-lg border border-border bg-surface p-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-foreground">{s.name}</span>
          {s.is_default && <Badge color="blue">{tp("badgeInitial")}</Badge>}
          {s.kind !== "standard" && <Badge color="gray">{t(`kind_${s.kind}`)}</Badge>}
          {s.schedule_state === "waiting" && <Badge color="gray">{t("scheduledBadge")}</Badge>}
          {s.last_job && (
            <Badge color={statusColor(s.last_job.status)}>
              {tj(`status_${s.last_job.status}`)}
            </Badge>
          )}
        </div>
        <p className="mt-0.5 line-clamp-1 text-sm text-muted" title={s.question}>
          {s.question}
        </p>
        {s.fields?.length ? (
          <p
            className="mt-0.5 truncate text-xs text-muted"
            title={`${t("fieldsLabel")}: ${s.fields.map((f) => f.label).join(", ")}`}
          >
            {t("fieldsLabel")}: {s.fields.map((f) => f.label).join(" · ")}
          </p>
        ) : null}
        <p className="mt-0.5 text-xs text-muted">
          {tp("lotSize", { n: s.lot_size })}
          {s.created_at ? ` · ${new Date(s.created_at).toLocaleDateString(locale)}` : ""}
        </p>
      </div>
      <Link href={withSearch(`/projects/${projectId}/articles`, s.id)}>
        <Button size="sm" variant="secondary">
          {t("open")}
        </Button>
      </Link>
    </li>
  );
}

function AnalyseContent() {
  const t = useTranslations("inference");
  const tLead = useTranslations("searches");
  const tj = useTranslations("jobs");
  const errText = useApiError();
  const locale = useLocale();
  const { user } = useAuth();
  const [isLead, setIsLead] = useState(false);
  const projectId = useParams<{ id: string }>().id;
  const searchId = useSearchId(projectId);
  const { searches, current } = useSearches(projectId, searchId);
  const selectSearch = useSelectSearch(projectId);
  // Benchmark prefill: ?model=<name>&prompt=<id>.
  const sp = useSearchParams();
  const wantedModel = sp.get("model");
  const wantedPrompt = sp.get("prompt");
  const openNew = sp.get("new") === "1";

  // "New search" panel: collapsed by default, expanded when there is nothing yet, or when
  // requested (?new=1) / prefilled from the Articles page.
  const [panelOpen, setPanelOpen] = useState(false);
  const [name, setName] = useState("");
  const [selection, setSelection] = useState<Selection>({});
  const [count, setCount] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [chain, setChain] = useState(false);

  const [job, setJob] = useState<InferenceJob | null>(null);
  const [busy, setBusy] = useState<InferenceJob | null>(null); // the project's in-progress analysis (all searches)
  const [notice, setNotice] = useState<string | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState<{ question: string; fields: SchemaField[]; sections: string[] } | null>(null);
  const [stage1, setStage1] = useState("");
  const [stage2, setStage2] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [promptId, setPromptId] = useState<string | null>(wantedPrompt);
  const [instructions, setInstructions] = useState("");
  const [sentPrompt, setSentPrompt] = useState<string | null>(null);
  const [promptTitle, setPromptTitle] = useState<string | null>(null);
  const [goldCount, setGoldCount] = useState<number | null>(null);
  const [examplesOn, setExamplesOn] = useState(false);
  const [exampleN, setExampleN] = useState(5);
  const [validate, setValidate] = useState(false);
  const progressRef = useRef<HTMLDivElement>(null);

  const active = job?.status === "PENDING" || job?.status === "RUNNING";
  const label = (m: ModelInfo) =>
    `${m.name}${m.ram_usage_mb ? ` · ~${m.ram_usage_mb} MB` : ""}`;

  const load = useCallback(() => {
    return Promise.all([getInference(projectId, searchId), getInference(projectId, undefined, true)])
      .then(([j, b]) => {
        setJob(j);
        setBusy(b);
        setError(null);
      })
      .catch((e) => {
        if (e instanceof Error && /404/.test(e.message)) setJob(null);
        else setError(errText(e));
      });
  }, [projectId, searchId, errText]);

  // Changing the search resets the analysis state (kept empty while it reloads).
  const searchKey = `${projectId}:${searchId}`;
  const [prevSearchKey, setPrevSearchKey] = useState(searchKey);
  if (prevSearchKey !== searchKey) {
    setPrevSearchKey(searchKey);
    setJob(null);
    setQ(null);
    setNotice(null);
  }

  // Changing the search reloads its question, its items and its analysis state.
  useEffect(() => {
    load();
    getProject(projectId, searchId)
      .then((p) => {
        const set = p.question_set !== false; // otherwise: no question, only a suggestion
        setQ({
          question: set ? (p.question ?? "") : "",
          fields: set ? (p.fields ?? []) : [],
          sections: p.text_sections ?? ["methods"],
        });
        setIsLead(isProjectLead(p, user));
      })
      .catch(() => {});
  }, [load, projectId, searchId, user]);

  useEffect(() => {
    listModels(projectId).then(setModels).catch(() => {});
  }, [projectId]);

  // The panel opens by default only when there is no search yet (fresh project) or when asked.
  const none = !!searches && searches.every(isUntouched);
  if ((none || openNew) && !panelOpen) setPanelOpen(true);

  // One-shot prefill pushed by the Articles page (client only: sessionStorage).
  const [prefillApplied, setPrefillApplied] = useState(false);
  if (!prefillApplied) {
    setPrefillApplied(true);
    try {
      const raw = sessionStorage.getItem(`diggy:newsearch:${projectId}`);
      if (raw) {
        sessionStorage.removeItem(`diggy:newsearch:${projectId}`);
        setSelection(JSON.parse(raw) as Selection);
        setPanelOpen(true);
      }
    } catch {
      /* no prefill */
    }
  }

  // Prompt from a benchmark: we show its title so the choice is visible.
  const [prevPromptId, setPrevPromptId] = useState(promptId);
  if (prevPromptId !== promptId) {
    setPrevPromptId(promptId);
    setPromptTitle(null);
  }
  useEffect(() => {
    if (!promptId) return;
    getPrompt(projectId, promptId)
      .then((p) => setPromptTitle(p.title ?? p.version_number))
      .catch(() => setPromptId(null));
  }, [projectId, promptId]);

  // Exact prompt of the last analysis (viewable under the progress).
  const tplId = job?.prompt_template_id;
  const [prevTplId, setPrevTplId] = useState(tplId);
  if (prevTplId !== tplId) {
    setPrevTplId(tplId);
    setSentPrompt(null);
  }
  useEffect(() => {
    if (!tplId) return;
    getPrompt(projectId, tplId)
      .then((p) => setSentPrompt(`${p.system_prompt ?? ""}\n\n${p.user_prompt_schema ?? ""}`.trim()))
      .catch(() => setSentPrompt(null));
  }, [projectId, tplId]);

  // Gold references of the search: without them, no examples and no validation.
  const [prevGoldKey, setPrevGoldKey] = useState(searchKey);
  if (prevGoldKey !== searchKey) {
    setPrevGoldKey(searchKey);
    setGoldCount(null);
  }
  useEffect(() => {
    listGold(projectId, searchId)
      .then((g) => setGoldCount(g.length))
      .catch(() => setGoldCount(null));
  }, [projectId, searchId]);
  const noGold = goldCount === 0;
  if (noGold && (examplesOn || validate)) {
    setExamplesOn(false);
    setValidate(false);
  }

  useEffect(() => {
    if (!active && !busy) return;
    const id = setInterval(() => load(), 3000);
    return () => clearInterval(id);
  }, [active, busy, load]);

  // Default selection: no stage 1; stage 2 = lightest model whose RAM is known.
  if (models.length > 0 && !stage2) {
    const wanted = models.find((m) => m.name === wantedModel);
    if (wanted) {
      setStage2(wanted.name);
    } else {
      const known = models.filter((m) => m.ram_usage_mb).sort((a, b) => a.ram_usage_mb! - b.ram_usage_mb!);
      setStage2((known[0] ?? models[0]).name);
    }
  }

  // A chained analysis is only offered while its base search is still being analysed.
  const chainable = !!current && (current.predicted ?? 0) < (current.lot_size ?? 0);
  const queued = (searches ?? []).filter((s) => s.schedule_state === "waiting");
  const workersBusy = !!busy && busy.search_id && busy.search_id !== current?.id;

  const goldOptions = () => ({
    ...(examplesOn ? { gold_examples: exampleN } : {}),
    ...(validate ? { validate_with_gold: true } : {}),
  });

  async function handleStop() {
    if (!window.confirm(t("stopConfirm"))) return;
    setSubmitting(true);
    setError(null);
    try {
      await cancelInference(projectId);
      await load();
    } catch (err) {
      setError(errText(err));
    } finally {
      setSubmitting(false);
    }
  }

  /** Re-runs the current search with its stored question (no new search created). */
  async function handleRerun() {
    if (!current) return;
    setSubmitting(true);
    setError(null);
    try {
      await triggerInference(projectId, {
        model: stage2 || stage1 || (job?.model_used ?? ""),
        ...(stage1 && stage2 && stage1 !== stage2 ? { prefilter_model: stage1 } : {}),
        ...(!promptId && instructions.trim() ? { instructions: instructions.trim() } : {}),
        ...goldOptions(),
        search_id: current.id,
      });
      await load();
    } catch (err) {
      setError(errText(err));
    } finally {
      setSubmitting(false);
    }
  }

  /** Creates a new search (from the corpus or from a previous search) and runs it — or chains it. */
  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if ((!stage2 && !stage1) || !q || !q.question.trim() || q.fields.length === 0 || q.sections.length === 0) return;
    setCreating(true);
    setError(null);
    try {
      const sel = cleanSelection(selection);
      const schedule = {
        model: stage2 || stage1,
        ...(stage2 && stage1 && stage2 !== stage1 ? { prefilter_model: stage1 } : {}),
        ...(instructions.trim() ? { instructions: instructions.trim() } : {}),
        selection: sel,
      };
      if (chain && current) {
        // Chained: the analysis starts once the base search has finished (one at a time per project).
        const s = await createSearch(projectId, {
          name: name.trim() || undefined,
          kind: "standard",
          schedule,
          question: q.question,
          fields: cleanFields(q.fields),
          text_sections: q.sections,
          selection: { from_search_id: current.id, ...sel },
        });
        storeSearchId(projectId, s.id);
        notifySearchesChanged();
        setChain(false);
        setName("");
        setSelection({});
        return;
      }
      const created = await createSearch(projectId, {
        name: name.trim() || undefined,
        kind: "standard",
        question: q.question,
        fields: cleanFields(q.fields),
        text_sections: q.sections,
        selection: sel,
      });
      storeSearchId(projectId, created.id);
      notifySearchesChanged();
      selectSearch(created.id);
      setPanelOpen(false);
      setName("");
      setSelection({});
      await triggerInference(projectId, {
        model: stage2 || stage1,
        ...(stage2 && stage1 && stage2 !== stage1 ? { prefilter_model: stage1 } : {}),
        ...(promptId ? { prompt_template_id: promptId } : {}),
        ...(!promptId && instructions.trim() ? { instructions: instructions.trim() } : {}),
        ...goldOptions(),
        search_id: created.id,
      });
      await load();
      const smooth =
        typeof window.matchMedia === "function" &&
        !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      setTimeout(
        () => progressRef.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" }),
        100
      );
    } catch (err) {
      setError(errText(err));
    } finally {
      setCreating(false);
    }
  }

  const r = job?.result ?? {};
  const jobModels =
    job && (
      <>
        <ModelRef name={job.model_used} identifier={job.model_info?.main?.identifier} version={job.model_info?.main?.version} />
        {job.prefilter_model && (
          <span className="block">
            {t("prefilterShort")}: <ModelRef name={job.prefilter_model} identifier={job.model_info?.prefilter?.identifier} version={job.model_info?.prefilter?.version} />
          </span>
        )}
      </>
    );
  const total = num(r.total);
  const needs = [
    !stage2 && !stage1 ? t("needModel") : null,
    !q || !q.question.trim() ? t("needQuestion") : null,
    q && q.fields.length === 0 ? t("needField") : null,
    q && q.sections.length === 0 ? t("needSection") : null,
    !chain && count === 0 ? t("needSelection") : null,
  ].filter((x): x is string => x !== null);

  const gauges = (
    <div className="flex flex-col gap-1">
      {workersBusy && busy && (
        <p className="text-xs text-warning">
          {t("workersBusy", { name: searches?.find((s) => s.id === busy.search_id)?.name ?? "…" })}
        </p>
      )}
      {queued.map((s) => (
        <p key={s.id} className="text-xs text-muted">
          {t("queuedNext", { name: s.name })}
        </p>
      ))}
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("intro")}</p>
      </div>

      {error && <ErrorBanner message={error} />}
      {notice && (
        <p className="rounded-lg border border-primary/40 bg-primary/5 p-3 text-sm text-foreground">{notice}</p>
      )}

      {isLead ? (
        <Card
          title={
            <span className="flex items-center gap-2">
              {t("panelTitle")}
              {panelOpen && (
                <Button type="button" variant="ghost" size="sm" onClick={() => setPanelOpen(false)}>
                  −
                </Button>
              )}
            </span>
          }
        >
          {panelOpen ? (
            <form onSubmit={handleCreate} className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <Field label={t("scopeNewName")}>
                  <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("scopeDefaultName")} />
                </Field>
                <ArticleSelector projectId={projectId} searches={searches ?? []} value={selection} onChange={setSelection} onCount={setCount} />
                <p className="text-sm font-medium text-foreground">
                  {count === null ? tLead("counting") : t("scopeCount", { n: count })}
                </p>
              </div>
              <QuestionEditor question={q?.question ?? ""} fields={q?.fields ?? []} sections={q?.sections ?? ["methods"]} onChange={setQ} />
              {promptId && (
                <div className="flex flex-wrap items-center gap-2 rounded-lg bg-primary/5 p-3 text-sm text-foreground">
                  <span>{t("promptChosen", { title: promptTitle ?? "…" })}</span>
                  <Button type="button" variant="ghost" size="sm" onClick={() => setPromptId(null)}>
                    {t("promptGenerated")}
                  </Button>
                </div>
              )}
              {isLead && models.length > 0 && (
                <div className="flex flex-col gap-4 rounded-lg border border-border p-3">
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="flex flex-col gap-2">
                      <p className="text-sm font-semibold text-foreground">{t("stage1")}</p>
                      <Select value={stage1} onChange={(e) => setStage1(e.target.value)}>
                        <option value="">{t("noPrefilter")}</option>
                        {models.map((m) => (
                          <option key={m.id} value={m.name}>
                            {label(m)}
                          </option>
                        ))}
                      </Select>
                      <span className="text-xs text-muted">{t("stage1Hint")}</span>
                    </div>
                    <div className="flex flex-col gap-2">
                      <p className="text-sm font-semibold text-foreground">{t("stage2")}</p>
                      <Select value={stage2} onChange={(e) => setStage2(e.target.value)}>
                        {stage1 && <option value="">{t("noStage2")}</option>}
                        {models.map((m) => (
                          <option key={m.id} value={m.name}>
                            {label(m)}
                          </option>
                        ))}
                      </Select>
                    </div>
                  </div>
                  <Field label={t("instructionsTitle")} hint={t("instructionsHint")}>
                    <Input value={instructions} onChange={(e) => setInstructions(e.target.value)} maxLength={2000} placeholder={t("instructionsPlaceholder")} />
                  </Field>
                  <div className="flex flex-col gap-2">
                    <p className="text-sm font-semibold text-foreground">{t("goldTitle")}</p>
                    <p className="text-xs text-muted">{noGold ? t("goldNone") : t("goldHint")}</p>
                    <div className="flex flex-wrap items-center gap-3">
                      <Checkbox label={t("useExamples")} checked={examplesOn} disabled={noGold} onChange={(e) => setExamplesOn(e.target.checked)} />
                      {examplesOn && (
                        <label className="flex items-center gap-2 text-sm text-foreground">
                          {t("exampleLabel")}
                          <input
                            type="number"
                            min={1}
                            max={MAX_GOLD_EXAMPLES}
                            value={exampleN}
                            onChange={(e) => setExampleN(Math.min(MAX_GOLD_EXAMPLES, Math.max(1, parseInt(e.target.value, 10) || 1)))}
                            className="w-20 rounded border border-border bg-surface p-1 text-sm text-foreground"
                          />
                        </label>
                      )}
                    </div>
                    <Checkbox label={t("validateGold")} checked={validate} disabled={noGold} onChange={(e) => setValidate(e.target.checked)} />
                    {goldCount !== null && !noGold && (
                      <p className="text-xs text-muted">{t("goldCount", { n: goldCount })}</p>
                    )}
                  </div>
                  {chainable && (
                    <div className="rounded-lg border border-border p-3">
                      <Checkbox label={t("chainLabel", { name: current?.name ?? "…" })} checked={chain} onChange={(e) => setChain(e.target.checked)} />
                      <p className="mt-1 text-xs text-muted">{t("chainHint")}</p>
                    </div>
                  )}
                  <div className="flex flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-3">
                      <Button
                        type="submit"
                        loading={creating}
                        disabled={!stage2 && !stage1 || !q || !q.question.trim() || q.fields.length === 0 || q.sections.length === 0 || (!chain && count === 0)}
                      >
                        {chain ? t("createScheduled") : t("run")}
                      </Button>
                      <Button type="button" variant="secondary" onClick={() => setPanelOpen(false)}>
                        {t("cancel")}
                      </Button>
                    </div>
                    {needs.length > 0 && (
                      <p className="text-sm text-muted">
                        {t("needsIntro")} {needs.join(" ")}
                      </p>
                    )}
                  </div>
                </div>
              )}
            </form>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-muted">{t("panelCollapsedHint")}</p>
              <Button variant="secondary" onClick={() => setPanelOpen(true)}>
                + {t("panelTitle")}
              </Button>
            </div>
          )}
        </Card>
      ) : (
        <p className="text-sm text-muted">{tLead("leadOnly")}</p>
      )}

      {/* Follow the analysis of the current search, with worker / chaining state on the gauge. */}
      {(job || active || workersBusy) && (
        <div ref={progressRef}>
          <Card title={t("progressTitle")}>
            {active || workersBusy ? (
              <JobProgress
                title={
                  <span className="font-mono text-xs text-muted">
                    {workersBusy
                      ? searches?.find((s) => s.id === busy?.search_id)?.name ?? "…"
                      : jobModels ?? "…"}
                  </span>
                }
                done={num(workersBusy ? busy?.result?.done : r.done)}
                total={num(workersBusy ? busy?.result?.total : total)}
                segments={{
                  accepted: num(workersBusy ? busy?.result?.auto_accepted : r.auto_accepted),
                  rejected: num(workersBusy ? busy?.result?.auto_rejected : r.auto_rejected),
                  review: num(workersBusy ? busy?.result?.needs_review : r.needs_review),
                }}
                action={
                  isLead && !workersBusy && (
                    <Button size="sm" variant="danger" loading={submitting} onClick={handleStop} title={t("stopHint")}>
                      ■ {t("stop")}
                    </Button>
                  )
                }
                hint={gauges}
              />
            ) : (
              <div className="mb-3 flex items-center justify-between gap-3 text-sm text-muted">
                <span className="font-mono">{jobModels}</span>
                <Badge color={statusColor(job?.status ?? "")}>{tj(`status_${job?.status ?? ""}`)}</Badge>
              </div>
            )}
            {sentPrompt && (
              <details className="mb-3 text-sm">
                <summary className="cursor-pointer text-xs font-medium text-muted">{t("promptSent")}</summary>
                <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-surface-muted p-3 text-xs text-foreground">{sentPrompt}</pre>
              </details>
            )}
            {!active && job?.status === "COMPLETED" && (
              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Stat label={t("processed")} value={num(r.processed ?? r.done)} />
                <Stat label={t("autoAccepted")} value={num(r.auto_accepted)} tone="bg-success/10" />
                <Stat label={t("autoRejected")} value={num(r.auto_rejected)} tone="bg-danger/10" />
                <Stat label={t("needsReview")} value={num(r.needs_review)} tone="bg-warning/10" />
              </div>
            )}
            {num(r.gold_examples) > 0 && (
              <p className="mt-2 text-xs text-muted">{t("goldExamplesUsed", { n: num(r.gold_examples) })}</p>
            )}
            {num(r.prefiltered) > 0 && (
              <p className="mt-2 text-xs text-muted">{t("prefilteredCount", { n: num(r.prefiltered) })}</p>
            )}
            {num(r.invalid_json) > 0 && (
              <p className="mt-2 text-xs text-warning">{t("invalidJson", { n: num(r.invalid_json) })}</p>
            )}
            {job?.status === "COMPLETED" && !!r.validation && (
              <div className="mt-4 rounded-lg border border-border p-3">
                <h3 className="mb-2 text-sm font-semibold text-foreground">{t("validationTitle")}</h3>
                <ValidationView scored={r.validation as Validation} primary={(r.validation as Validation).primary} fields={q?.fields ?? []} />
              </div>
            )}
            {job?.status === "FAILED" && job.error === "cancelled" && (
              <p className="mt-3 rounded-lg bg-warning/10 p-3 text-sm text-foreground">{t("stopped", { n: num(r.done) })}</p>
            )}
            {job?.status === "FAILED" && job.error && job.error !== "cancelled" && (
              <p className="mt-3 rounded-lg bg-danger/10 p-3 text-sm text-danger">{errText(job.error)}</p>
            )}
            {job?.status === "FAILED" && (
              <div className="mt-3 flex flex-col items-start gap-1">
                <Button loading={submitting} onClick={handleStop}>
                  ↻ {t("resume")}
                </Button>
                <span className="text-xs text-muted">{t("resumeHint", { n: num(r.done) })}</span>
              </div>
            )}
            {job?.status === "COMPLETED" && (
              <div className="mt-4 flex flex-wrap gap-2">
                <Link href={withSearch(`/projects/${projectId}/articles`, searchId)}>
                  <Button variant="secondary">{t("seeResults")}</Button>
                </Link>
                <ExportMenu projectId={projectId} />
                {num(r.needs_review) > 0 && (
                  <Link href={withSearch(`/projects/${projectId}/annotate?queue=review`, searchId)}>
                    <Button>{t("annotateReview")}</Button>
                  </Link>
                )}
              </div>
            )}
            {isLead && current && (current.predicted ?? 0) + (current.reviewed ?? 0) > 0 && !active && !workersBusy && (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="ghost" loading={submitting} onClick={() => void handleRerun()}>
                  ↻ {t("relaunch")}
                </Button>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* Gold standard & benchmark: how the references feed the analysis, with links. */}
      <Card title={t("goldSectionTitle")}>
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <p className="text-sm text-foreground">{t("goldExplained")}</p>
            <Link href={`/projects/${projectId}/gold`} className="mt-2 inline-block text-sm font-medium text-primary hover:underline">
              {t("goldLink")} →
            </Link>
          </div>
          <div>
            <p className="text-sm text-foreground">{t("benchmarkExplained")}</p>
            <Link href={`/projects/${projectId}/benchmarks`} className="mt-2 inline-block text-sm font-medium text-primary hover:underline">
              {t("benchmarkLink")} →
            </Link>
          </div>
        </div>
      </Card>

      {/* All the analyses performed, each with a link to its results. */}
      {searches && searches.some((s) => !isUntouched(s)) ? (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-foreground">{t("listTitle")}</h2>
          <ul className="flex flex-col gap-2">
            {searches
              .filter((s) => !isUntouched(s))
              .map((s) => (
                <AnalysisRow key={s.id} s={s} projectId={projectId} locale={locale} />
              ))}
          </ul>
        </div>
      ) : searches ? (
        // The creation panel (above) is already open for a lead on a fresh project:
        // the empty state would repeat it. Members without the form still see it.
        isLead && panelOpen ? null : (
          <EmptyState
            title={tLead("empty")}
            hint={tLead("emptyHint")}
            action={
              isLead ? (
                <Button variant="secondary" onClick={() => setPanelOpen(true)}>
                  + {t("panelTitle")}
                </Button>
              ) : undefined
            }
          />
        )
      ) : (
        <Skeleton rows={3} />
      )}
    </div>
  );
}

export default function InferencePage() {
  return (
    <RequireAuth>
      <AnalyseContent />
    </RequireAuth>
  );
}
