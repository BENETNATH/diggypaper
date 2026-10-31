"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { isProjectLead } from "@/lib/role";
import { ModelRef } from "@/components/ModelRef";
import {
  updateProject,
  triggerInference,
  cancelInference,
  getInference,
  getProject,
  getPrompt,
  listGold,
  listModels,
  InferenceJob,
  ModelInfo,
  SchemaField,
  Validation,
  createSearch,
  Selection,
} from "@/lib/api";
import { useApiError } from "@/lib/errorMsg";
import { MAX_GOLD_EXAMPLES } from "@/lib/benchmark";
import { ExportMenu } from "@/components/ExportMenu";
import { RequireAuth } from "@/components/RequireAuth";
import { QuestionEditor } from "@/components/QuestionEditor";
import { Button } from "@/components/ui/Button";
import { Checkbox, Field, Input, Select } from "@/components/ui/Field";
import { ValidationView } from "@/components/ValidationView";
import { cleanSelection, isUntouched, withSearch } from "@/lib/search";
import { notifySearchesChanged, storeSearchId, useSearchId, useSearches, useSelectSearch } from "@/lib/useSearch";
import { ArticleSelector } from "@/components/ArticleSelector";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { ErrorBanner } from "@/components/ui/Feedback";
import { JobProgress } from "@/components/JobProgress";

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
  const tLead = useTranslations("searches"); // leadOnly notice
  const tj = useTranslations("jobs");
  const errText = useApiError(); // raw API error → i18n message
  const { user } = useAuth();
  const [isLead, setIsLead] = useState(false);
  const projectId = useParams<{ id: string }>().id;
  const searchId = useSearchId(projectId);
  const { searches, current } = useSearches(projectId, searchId);
  const selectSearch = useSelectSearch(projectId);
  // Which articles: an existing search, or a new selection created at launch.
  const [scopeMode, setScopeMode] = useState<"existing" | "new">("existing");
  const [newSel, setNewSel] = useState<Selection>({});
  const [newName, setNewName] = useState("");
  const [newCount, setNewCount] = useState<number | null>(null);
  // Prefill from a benchmark: ?model=<name>&prompt=<id of the saved prompt>.
  const sp = useSearchParams();
  const wantedModel = sp.get("model");
  const wantedPrompt = sp.get("prompt");

  const [job, setJob] = useState<InferenceJob | null>(null);
  const [busy, setBusy] = useState<InferenceJob | null>(null); // the project's analysis in progress (all searches)
  const [notice, setNotice] = useState<string | null>(null);
  const [saved0, setSaved0] = useState("");
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState<{
    question: string;
    fields: SchemaField[];
    sections: string[];
  } | null>(null);
  const [stage1, setStage1] = useState("");
  const [stage2, setStage2] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [launched, setLaunched] = useState(false);
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
    setLaunched(false);
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
        setSaved0(JSON.stringify([set ? (p.question ?? "") : "", set ? (p.fields ?? []) : []]));
        setIsLead(isProjectLead(p, user));
      })
      .catch(() => {});
  }, [load, projectId, searchId, user]);

  useEffect(() => {
    listModels(projectId).then(setModels).catch(() => {});
  }, [projectId]);

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
      .catch(() => setPromptId(null)); // prompt not found: back to the generated prompt
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
  // Without gold references, the example and validation toggles are unavailable.
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

  /** Resumes an interrupted analysis: the already-analyzed articles are skipped. */
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

  const goldOptions = () => ({
    ...(examplesOn ? { gold_examples: exampleN } : {}),
    ...(validate ? { validate_with_gold: true } : {}),
  });

  async function handleResume() {
    if (!job) return;
    setSubmitting(true);
    setError(null);
    try {
      await triggerInference(projectId, {
        model: job.model_used,
        ...(job.prefilter_model ? { prefilter_model: job.prefilter_model } : {}),
        ...(job.prompt_template_id ? { prompt_template_id: job.prompt_template_id } : {}),
        ...goldOptions(),
        resume: true,
        ...(searchId ? { search_id: searchId } : {}),
      });
      await load();
    } catch (err) {
      setError(errText(err));
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
      const cleanFields = q.fields.map((f) => ({
        ...f,
        options: f.type === "choice" ? (f.options ?? []).filter(Boolean) : undefined,
      }));
      let runSearchId = searchId;
      if (scopeMode === "new") {
        // New selection: we create the search (with the displayed question), then analyze it.
        const created = await createSearch(projectId, {
          name: newName.trim() || undefined,
          question: q.question,
          fields: cleanFields,
          text_sections: q.sections,
          kind: "standard",
          selection: { from_search_id: searchId, ...cleanSelection(newSel) },
        });
        runSearchId = created.id;
        storeSearchId(projectId, created.id);
        notifySearchesChanged();
        setScopeMode("existing");
        setNewSel({});
        setNewName("");
        selectSearch(created.id);
      } else if (
        (current?.predicted ?? 0) + (current?.reviewed ?? 0) > 0 &&
        JSON.stringify([q.question, cleanFields]) !== saved0
      ) {
        // The question of an already-analyzed or re-reviewed search can no longer change: new search, same batch.
        const created = await createSearch(projectId, {
          question: q.question,
          fields: cleanFields,
          text_sections: q.sections,
          kind: "standard",
          selection: { from_search_id: searchId },
        });
        runSearchId = created.id;
        storeSearchId(projectId, created.id);
        notifySearchesChanged();
        selectSearch(created.id);
        setNotice(t("questionLocked", { name: created.name }));
      } else {
        // The question is saved at launch (a single step for the user).
        await updateProject(projectId, {
          question: q.question,
          fields: cleanFields,
          text_sections: q.sections,
        }, searchId);
      }
      // Empty stage 2: stage 1 decides alone.
      await triggerInference(projectId, {
        model: stage2 || stage1,
        ...(stage2 && stage1 && stage2 !== stage1 ? { prefilter_model: stage1 } : {}),
        ...(promptId ? { prompt_template_id: promptId } : {}),
        ...(!promptId && instructions.trim() ? { instructions: instructions.trim() } : {}),
        ...goldOptions(),
        ...(runSearchId ? { search_id: runSearchId } : {}),
      });
      await load();
      setLaunched(true);
      // The progress area is below the fold: we bring the user there (no smooth scroll when reduced motion).
      const smooth = typeof window.matchMedia === "function" && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      setTimeout(() => progressRef.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" }), 100);
    } catch (err) {
      setError(errText(err));
    } finally {
      setSubmitting(false);
    }
  }

  const r = job?.result ?? {};
  // Traced models of the analysis (frozen snapshot): main + possible pre-filter.
  const jobModels = job && (
    <>
      <ModelRef
        name={job.model_used}
        identifier={job.model_info?.main?.identifier}
        version={job.model_info?.main?.version}
      />
      {job.prefilter_model && (
        <span className="block">
          {t("prefilterShort")}:{" "}
          <ModelRef
            name={job.prefilter_model}
            identifier={job.model_info?.prefilter?.identifier}
            version={job.model_info?.prefilter?.version}
          />
        </span>
      )}
    </>
  );
  const total = num(r.total);
  const busyElsewhere = !!busy && busy.id !== job?.id;
  // Why the run button is disabled: the unmet conditions, in plain language.
  const needs = [
    !stage2 && !stage1 ? t("needModel") : null,
    !q || !q.question.trim() ? t("needQuestion") : null,
    q && q.fields.length === 0 ? t("needField") : null,
    q && q.sections.length === 0 ? t("needSection") : null,
    scopeMode === "new" && !newCount ? t("needSelection") : null,
  ].filter((x): x is string => x !== null);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("intro")}</p>
      </div>

      {error && <ErrorBanner message={error} />}
      {notice && <p className="rounded-lg border border-primary/40 bg-primary/5 p-3 text-sm text-foreground">{notice}</p>}
      {busyElsewhere && (
        <p className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-foreground">
          {t("busyElsewhere", { name: searches?.find((s) => s.id === busy?.search_id)?.name ?? "…" })}
        </p>
      )}

      {searches && searches.length > 0 && (
        <Card title={t("scopeTitle")}>
          <div className="mb-3 flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input type="radio" checked={scopeMode === "existing"} onChange={() => setScopeMode("existing")} disabled={active} />
              {searches.every(isUntouched) ? t("scopeCorpus") : t("scopeExisting")}
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" checked={scopeMode === "new"} onChange={() => setScopeMode("new")} disabled={active} />
              {searches.every(isUntouched) ? t("scopeCorpusSelection") : t("scopeNew")}
            </label>
          </div>
          {scopeMode === "existing" && searches.every(isUntouched) ? (
            <p className="text-sm font-medium text-foreground">{t("scopeCount", { n: current?.lot_size ?? 0 })}</p>
          ) : scopeMode === "existing" ? (
            <Field label={t("searchTitle")} hint={t("searchHint")}>
              <Select value={current?.id ?? ""} onChange={(e) => selectSearch(e.target.value)} disabled={active}>
                {searches.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} — {t("lotSize", { n: s.lot_size })}
                  </option>
                ))}
              </Select>
            </Field>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-xs text-muted">{t("scopeNewHint")}</p>
              <Field label={t("scopeNewName")}>
                <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder={t("scopeDefaultName")} />
              </Field>
              <ArticleSelector projectId={projectId} searches={searches} value={{ from_search_id: searchId, ...newSel }} onChange={(s) => setNewSel(s)} onCount={setNewCount} />
              <p className="text-sm font-medium text-foreground">
                {newCount === null ? "…" : t("scopeCount", { n: newCount })}
              </p>
            </div>
          )}
        </Card>
      )}

      <Card title={`1. ${t("questionTitle")}`}>
        {q ? (
          <QuestionEditor {...q} onChange={setQ} />
        ) : (
          <p className="text-sm text-muted">…</p>
        )}
      </Card>

      <Card title={`2. ${t("cascadeTitle")}`}>
        {!isLead ? (
          <p className="text-sm text-muted">{tLead("leadOnly")}</p>
        ) : models.length === 0 ? (
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
            {promptId && (
              <div className="flex flex-wrap items-center gap-2 rounded-lg bg-primary/5 p-3 text-sm text-foreground">
                <span>{t("promptChosen", { title: promptTitle ?? "…" })}</span>
                <Button type="button" variant="ghost" size="sm" onClick={() => setPromptId(null)} disabled={active}>
                  {t("promptGenerated")}
                </Button>
              </div>
            )}
            {!promptId && (
              <details className="rounded-lg border border-border p-3" open={instructions.length > 0}>
                <summary className="cursor-pointer text-sm font-semibold text-foreground">{t("instructionsTitle")}</summary>
                <p className="mb-2 mt-1 text-xs text-muted">{t("instructionsHint")}</p>
                <textarea
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  maxLength={2000}
                  rows={3}
                  disabled={active}
                  placeholder={t("instructionsPlaceholder")}
                  className="w-full rounded border border-border bg-surface p-2 text-sm text-foreground"
                />
              </details>
            )}
            <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
              <p className="text-sm font-semibold text-foreground">{t("goldTitle")}</p>
              <p className="text-xs text-muted">{noGold ? t("goldNone") : t("goldHint")}</p>
              <div className="flex flex-wrap items-center gap-3">
                <Checkbox
                  label={t("useExamples")}
                  checked={examplesOn}
                  disabled={noGold || active}
                  onChange={(e) => setExamplesOn(e.target.checked)}
                />
                {examplesOn && (
                  <label className="flex items-center gap-2 text-sm text-foreground">
                    {t("exampleLabel")}
                    <input
                      type="number"
                      min={1}
                      max={MAX_GOLD_EXAMPLES}
                      value={exampleN}
                      onChange={(e) =>
                        setExampleN(Math.min(MAX_GOLD_EXAMPLES, Math.max(1, parseInt(e.target.value, 10) || 1)))
                      }
                      className="w-20 rounded border border-border bg-surface p-1 text-sm text-foreground"
                    />
                  </label>
                )}
              </div>
              {examplesOn && <p className="text-xs text-muted">{t("examplesHint")}</p>}
              <Checkbox
                label={t("validateGold")}
                checked={validate}
                disabled={noGold || active}
                onChange={(e) => setValidate(e.target.checked)}
              />
              {validate && <p className="text-xs text-muted">{t("validateHint")}</p>}
              {goldCount !== null && !noGold && <p className="text-xs text-muted">{t("goldCount", { n: goldCount })}</p>}
            </div>
            <div className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" loading={submitting} disabled={active || busyElsewhere || (!stage2 && !stage1) || !q || !q.question.trim() || q.fields.length === 0 || q.sections.length === 0 || (scopeMode === "new" && !newCount)}>
                  {job ? t("rerun") : t("run")}
                </Button>
                {launched && active && <span className="text-sm text-success">✓ {t("launched")}</span>}
              </div>
              {needs.length > 0 && (
                <p className="text-sm text-muted">
                  {t("needsIntro")} {needs.join(" ")}
                </p>
              )}
            </div>
          </form>
        )}
      </Card>

      {job && (
        <div ref={progressRef}>
        <Card title={`3. ${t("progressTitle")}`}>
          {active ? (
            <JobProgress
              title={<span className="font-mono text-xs text-muted">{jobModels}</span>}
              done={num(r.done)}
              total={total}
              segments={{
                accepted: num(r.auto_accepted),
                rejected: num(r.auto_rejected),
                review: num(r.needs_review),
              }}
              action={
                <Button size="sm" variant="danger" loading={submitting} onClick={handleStop} title={t("stopHint")}>
                  ■ {t("stop")}
                </Button>
              }
              hint={num(r.done) === 0 ? t("firstRunNote") : t("stopHint")}
            />
          ) : (
            <div className="mb-3 flex items-center justify-between gap-3 text-sm text-muted">
              <span className="font-mono">{jobModels}</span>
              <Badge color={statusColor(job.status)}>{tj(`status_${job.status}`)}</Badge>
            </div>
          )}
          {sentPrompt && (
            <details className="mb-3 text-sm">
              <summary className="cursor-pointer text-xs font-medium text-muted">{t("promptSent")}</summary>
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-surface-muted p-3 text-xs text-foreground">
                {sentPrompt}
              </pre>
            </details>
          )}
          {!active && job.status === "COMPLETED" && (
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
          {job.status === "COMPLETED" && !!r.validation && (
            <div className="mt-4 rounded-lg border border-border p-3">
              <h3 className="mb-2 text-sm font-semibold text-foreground">{t("validationTitle")}</h3>
              <ValidationView scored={r.validation as Validation} primary={(r.validation as Validation).primary} fields={q?.fields ?? []} />
            </div>
          )}
          {job.status === "FAILED" && job.error === "cancelled" && (
            <p className="mt-3 rounded-lg bg-warning/10 p-3 text-sm text-foreground">{t("stopped", { n: num(r.done) })}</p>
          )}
          {job.status === "FAILED" && job.error && job.error !== "cancelled" && (
            <p className="mt-3 rounded-lg bg-danger/10 p-3 text-sm text-danger">{errText(job.error)}</p>
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
