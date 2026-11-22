"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useParams, useRouter, useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { isProjectLead, roleFromList } from "@/lib/role";
import { ModelRef } from "@/components/ModelRef";
import {
  cancelInference,
  createSearch,
  deleteSearchJob,
  getInference,
  getOverview,
  getProject,
  listGold,
  listModels,
  listSearchJobs,
  rerunSearch,
  triggerInference,
  updateProject,
  InferenceJob,
  ModelInfo,
  SchemaField,
  SearchCreate,
  Selection,
} from "@/lib/api";
import { useApiError } from "@/lib/errorMsg";
import { MAX_GOLD_EXAMPLES } from "@/lib/benchmark";
import { RequireAuth } from "@/components/RequireAuth";
import { QuestionEditor } from "@/components/QuestionEditor";
import { ArticleSelector } from "@/components/ArticleSelector";
import { CorporaEmpty } from "@/components/CorporaEmpty";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Checkbox, Field, Input, Select } from "@/components/ui/Field";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { EmptyState, ErrorBanner, Skeleton } from "@/components/ui/Feedback";
import { JobProgress } from "@/components/JobProgress";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { ChatRagPanel } from "@/components/ChatRagPanel";
import { smoothScrollIntoView } from "@/lib/motion";
import { cleanSelection, nextAutoName, runStatusKey, withSearch } from "@/lib/search";
import {
  notifySearchesChanged,
  storeSearchId,
  useSearchId,
  useSearches,
} from "@/lib/useSearch";

const num = (v: unknown): number => (typeof v === "number" ? v : 0);
const cleanFields = (fields: SchemaField[]): SchemaField[] =>
  fields.map((f) => ({ ...f, options: f.type === "choice" ? (f.options ?? []).filter(Boolean) : undefined }));
const runActive = (j: InferenceJob): boolean => j.status === "PENDING" || j.status === "RUNNING";

/** « Relancer ▾ » of one run: overwrite in place (model choice) or open the launch panel. */
function RerunMenu({
  job,
  models,
  submitting,
  onOverwrite,
  onNamed,
}: {
  job: InferenceJob;
  models: ModelInfo[];
  submitting: boolean;
  onOverwrite: (model: string) => void;
  onNamed: () => void;
}) {
  const t = useTranslations("inference");
  const label = (m: ModelInfo) => `${m.name}${m.ram_usage_mb ? ` · ~${m.ram_usage_mb} MB` : ""}`;
  const [model, setModel] = useState<string | null>(null); // null: the run's own model
  return (
    <Menu label={t("rerunMenu")}>
      <MenuItem disabled={submitting} onClick={() => onOverwrite(model ?? job.model_used)}>
        <span className="font-medium">{t("runOverwrite")}</span>
        <span className="block text-xs text-muted">{t("runOverwriteSub")}</span>
      </MenuItem>
      {/* the model choice does not close the menu: the overwrite action is chosen after it */}
      <div onClick={(e) => e.stopPropagation()} className="px-3 py-2">
        <p className="mb-1 text-xs font-medium text-muted">{t("model")}</p>
        <Select value={model ?? job.model_used} onClick={(e) => e.stopPropagation()} onChange={(e) => setModel(e.target.value)}>
          {models.map((m) => (
            <option key={m.id} value={m.name}>
              {label(m)}
            </option>
          ))}
        </Select>
      </div>
      <MenuSeparator />
      <MenuItem disabled={submitting} onClick={onNamed}>
        <span className="font-medium">{t("runNamed")}</span>
        <span className="block text-xs text-muted">{t("runNamedSub")}</span>
      </MenuItem>
    </Menu>
  );
}

/** One run of the corpus: identity, progress/state, actions (spec 2026-11-20-analyses-runs-2 R3). */
function RunRow({
  j,
  models,
  lot,
  locale,
  isLead,
  seeResultsHref,
  onChat,
  submitting,
  onOverwrite,
  onNamed,
  onDelete,
  onStop,
  onResume,
}: {
  j: InferenceJob;
  models: ModelInfo[];
  lot: number;
  locale: string;
  isLead: boolean;
  seeResultsHref: string;
  onChat: () => void;
  submitting: boolean;
  onOverwrite: (model: string) => void;
  onNamed: () => void;
  onDelete: () => void;
  onStop: () => void;
  onResume: () => void;
}) {
  const t = useTranslations("inference");
  const tj = useTranslations("jobs");
  const tc = useTranslations("common");
  const status = runStatusKey(j);
  const x = j.n_predictions ?? num(j.result?.done);
  const y = num(j.result?.total) || lot;
  const date = j.created_at ? new Date(j.created_at).toLocaleDateString(locale) : null;
  // COMPLETED only: the triage breakdown, non-zero parts joined (spec R3).
  const parts: string[] = [];
  if (j.status === "COMPLETED") {
    const b = j.breakdown ?? {};
    if (b.AUTO_ACCEPTED) parts.push(t("runBreakAccepted", { n: b.AUTO_ACCEPTED }));
    if (b.AUTO_REJECTED) parts.push(t("runBreakRejected", { n: b.AUTO_REJECTED }));
    if (b.NEEDS_HUMAN_REVIEW) parts.push(t("runBreakReview", { n: b.NEEDS_HUMAN_REVIEW }));
    if (b.NONE) parts.push(t("runBreakNone", { n: b.NONE }));
  }
  const failed = j.status === "FAILED" && j.error !== "cancelled";
  return (
    <li className="rounded-lg bg-surface-muted/50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-foreground">{j.name ?? t("runUnnamed")}</span>
            <Badge color={statusColor(status)}>{tj(`status_${status}`)}</Badge>
          </div>
          <p className="mt-0.5 text-xs text-muted">
            <ModelRef name={j.model_used} identifier={j.model_info?.main?.identifier} version={j.model_info?.main?.version} />
            {" · "}
            <span>{t("runLot", { n: lot })}</span>
            {" · "}
            <span>{t("runAnalysed", { x, y })}</span>
            {date ? (
              <>
                {" · "}
                <span>{date}</span>
              </>
            ) : null}
          </p>
          {parts.length > 0 && (
            <p className="mt-0.5 text-xs text-muted">
              {parts.map((p, i) => (
                <Fragment key={i}>
                  {i > 0 ? " · " : null}
                  <span>{p}</span>
                </Fragment>
              ))}
            </p>
          )}
          {runActive(j) && (
            <div className="mt-2 max-w-md">
              <JobProgress inline done={x} total={y} />
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Link href={seeResultsHref}>
            <Button size="sm" variant="secondary">
              {t("seeResults")}
            </Button>
          </Link>
          {j.status === "COMPLETED" && (
            <Button size="sm" variant="ghost" onClick={onChat}>
              {t("ctaChat")}
            </Button>
          )}
          {isLead && (
            <RerunMenu job={j} models={models} submitting={submitting} onOverwrite={onOverwrite} onNamed={onNamed} />
          )}
          {isLead && (
            <Button size="sm" variant="ghost" onClick={onDelete}>
              {tc("delete")}
            </Button>
          )}
          {isLead && runActive(j) && (
            <Button size="sm" variant="danger" loading={submitting} onClick={onStop} title={t("stopHint")}>
              {t("stop")}
            </Button>
          )}
          {isLead && failed && (
            <Button size="sm" variant="secondary" loading={submitting} onClick={onResume}>
              {t("resume")}
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}

function AnalyseContent() {
  const t = useTranslations("inference");
  const tq = useTranslations("question");
  const tLead = useTranslations("searches");
  const tp = useTranslations("project");
  const tc = useTranslations("common");
  const errText = useApiError();
  const locale = useLocale();
  const { user } = useAuth();
  const projectId = useParams<{ id: string }>().id;
  const pathname = usePathname();
  const router = useRouter();
  const sp = useSearchParams();
  const searchId = useSearchId(projectId);
  const { searches, current } = useSearches(projectId, searchId);
  // The creation panel opens when asked (?new=1, from the pill CTA / overview / S1).
  const openNew = sp.get("new") === "1";

  const [isLead, setIsLead] = useState(false);
  const [articleCount, setArticleCount] = useState<number | null>(null);
  const [jobs, setJobs] = useState<InferenceJob[] | null>(null);
  const [q, setQ] = useState<{ question: string; fields: SchemaField[]; sections: string[] } | null>(null);
  const [busy, setBusy] = useState<InferenceJob | null>(null); // the project's in-progress job (one per project)
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Launch panel (« Nouvelle analyse »): output name / overwrite, gold options, chaining.
  const [launchOpen, setLaunchOpen] = useState(false);
  const [launchModel, setLaunchModel] = useState("");
  const [instructions, setInstructions] = useState("");
  const [goldCount, setGoldCount] = useState<number | null>(null);
  const [examplesOn, setExamplesOn] = useState(false);
  const [exampleN, setExampleN] = useState(5);
  const [validate, setValidate] = useState(false);
  const [chain, setChain] = useState(false);
  const [outMode, setOutMode] = useState<"new" | "overwrite">("new");
  const [runName, setRunName] = useState<string | null>(null); // null: the derived auto name
  const [overwriteId, setOverwriteId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Corpus creation / edition panel (spec R3/R4).
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [createQ, setCreateQ] = useState<{ question: string; fields: SchemaField[]; sections: string[] } | null>(null);
  const [createName, setCreateName] = useState<string | null>(null); // null: the derived auto name
  const [createSelection, setCreateSelection] = useState<Selection>({});
  const [count, setCount] = useState<number | null>(null);
  // Chat RAG (spec 2026-11-07-chat-rag §5) : interroger tout le corpus du projet,
  // ou le lot d'une recherche. Disclosure repliée par défaut (spec clarte-3 R3 :
  // une page, une intention). Restauration 2026-11-22 (orphelinée par le rewrite
  // analyses-runs 7da19f3).
  const [chatRequest, setChatRequest] = useState<{ searchId: string; nonce: number } | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const chatRef = useRef<HTMLDetailsElement>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);

  const [stopOpen, setStopOpen] = useState(false);
  const [deleteJob, setDeleteJob] = useState<InferenceJob | null>(null);

  // Changing the corpus resets its derived state (kept empty while it reloads).
  const corpusKey = current?.id ?? "none";
  const [prevCorpusKey, setPrevCorpusKey] = useState(corpusKey);
  if (prevCorpusKey !== corpusKey) {
    setPrevCorpusKey(corpusKey);
    setJobs(null);
    setQ(null);
    setBusy(null);
    setGoldCount(null);
    setLaunchOpen(false);
    setInstructions("");
    setExamplesOn(false);
    setValidate(false);
    setChain(false);
    setOutMode("new");
    setRunName(null);
    setOverwriteId("");
    setCreateQ(null);
    setCreateName(null);
    setCreateSelection({});
    setCount(null);
    setEditOpen(false);
  }
  // ?new=1 opens the creation panel (the layout CTA, the overview CTAs, the S1 CTA).
  if (openNew && !createOpen) setCreateOpen(true);

  // The corpus of the current search: runs, question, role, gold references.
  const corpusId = current?.id;
  useEffect(() => {
    if (!corpusId) return;
    let active = true;
    listSearchJobs(projectId, corpusId)
      .then((r) => {
        if (active) setJobs(r.jobs);
      })
      .catch(() => {
        if (active) setJobs([]); // 404: the corpus was deleted (the selection falls back)
      });
    getProject(projectId, corpusId)
      .then((p) => {
        if (!active) return;
        const set = p.question_set !== false;
        setQ({
          question: set ? (p.question ?? "") : "",
          fields: set ? (p.fields ?? []) : [],
          sections: p.text_sections ?? ["methods"],
        });
        setIsLead(isProjectLead(p, user));
        setArticleCount(p.article_count ?? null);
      })
      .catch(() => {});
    listGold(projectId, corpusId)
      .then((g) => {
        if (active) setGoldCount(g.length);
      })
      .catch(() => {
        if (active) setGoldCount(null);
      });
    return () => {
      active = false;
    };
  }, [projectId, corpusId, user]);

  // No corpus at all (spec R2): the role comes from the project list and the article
  // count from the overview — never a search-scoped call (the backend 404s).
  useEffect(() => {
    if (searches === null || searches.length > 0) return;
    let active = true;
    getOverview(projectId)
      .then((o) => {
        if (active) setArticleCount(o.articles);
      })
      .catch(() => {});
    roleFromList(projectId)
      .then((r) => {
        if (active) setIsLead(r === "LEAD" || user?.role === "ADMIN");
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [projectId, searches, user]);

  useEffect(() => {
    listModels(projectId).then(setModels).catch(() => {});
  }, [projectId]);

  // The project's in-progress job (the one-per-project guard): read on mount of a corpus,
  // re-read while one is active.
  const loadBusy = useCallback(
    () =>
      getInference(projectId, undefined, true)
        .then(setBusy)
        .catch(() => {}),
    [projectId]
  );
  useEffect(() => {
    if (!corpusId) return;
    loadBusy();
  }, [corpusId, loadBusy]);
  const busyActive = !!busy && runActive(busy);
  useEffect(() => {
    if (!corpusId || !busyActive) return;
    const id = setInterval(loadBusy, 3000);
    return () => clearInterval(id);
  }, [corpusId, busyActive, loadBusy]);

  // Default launch model: the lightest known (same rule as before).
  if (models.length > 0 && !launchModel) {
    const known = models.filter((m) => m.ram_usage_mb).sort((a, b) => a.ram_usage_mb! - b.ram_usage_mb!);
    setLaunchModel((known[0] ?? models[0]).name);
  }
  // Overwrite mode: the first finished run is preselected (active runs are disabled).
  if (outMode === "overwrite" && !overwriteId && jobs?.length) {
    const first = jobs.find((j) => !runActive(j));
    if (first) setOverwriteId(first.id);
  }
  const noGold = goldCount === 0;
  if (noGold && (examplesOn || validate)) {
    setExamplesOn(false);
    setValidate(false);
  }

  const goldOptions = () => ({
    ...(examplesOn ? { gold_examples: exampleN } : {}),
    ...(validate ? { validate_with_gold: true } : {}),
  });

  const reloadJobs = useCallback(() => {
    if (!corpusId) return;
    listSearchJobs(projectId, corpusId).then((r) => setJobs(r.jobs)).catch(() => {});
  }, [projectId, corpusId]);

  /** Overwrite a run in place (Relancer ▾): same lot, results replaced (spec R3). */
  async function handleOverwrite(job: InferenceJob, model: string) {
    if (!corpusId) return;
    setSubmitting(true);
    setError(null);
    try {
      await rerunSearch(projectId, corpusId, model, { overwrite_job_id: job.id });
      notifySearchesChanged();
      reloadJobs();
      loadBusy();
    } catch (err) {
      setError(errText(err));
    } finally {
      setSubmitting(false);
    }
  }

  /** Delete a finished run and its results (spec R3). */
  async function handleDeleteJob() {
    if (!deleteJob || !corpusId) return;
    setSubmitting(true);
    setError(null);
    try {
      await deleteSearchJob(projectId, corpusId, deleteJob.id);
      setDeleteJob(null);
      notifySearchesChanged();
      reloadJobs();
    } catch (err) {
      setError(errText(err));
    } finally {
      setSubmitting(false);
    }
  }

  /** Resume the failed predictions of a run (same configuration). */
  async function handleResume(job: InferenceJob) {
    if (!corpusId) return;
    setSubmitting(true);
    setError(null);
    try {
      await triggerInference(projectId, { model: job.model_used, search_id: corpusId, resume: true });
      notifySearchesChanged();
      reloadJobs();
      loadBusy();
    } catch (err) {
      setError(errText(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function confirmStop() {
    setStopOpen(false);
    setSubmitting(true);
    setError(null);
    try {
      await cancelInference(projectId);
      loadBusy();
      reloadJobs();
    } catch (err) {
      setError(errText(err));
    } finally {
      setSubmitting(false);
    }
  }

  /** Create a corpus of questions (spec R4): question + fields + lot, no launch. */
  async function handleCreateCorpus(e: React.SyntheticEvent) {
    e.preventDefault();
    if (count === 0) return;
    const ed = createQ ?? q ?? { question: "", fields: [], sections: ["methods"] };
    const shownName = createName ?? nextAutoName((searches ?? []).map((s) => s.name));
    setCreating(true);
    setError(null);
    try {
      const payload: SearchCreate = {
        kind: "standard",
        selection: cleanSelection(createSelection),
        ...(shownName.trim() ? { name: shownName.trim() } : {}),
        ...(ed.question.trim() ? { question: ed.question.trim() } : {}),
        ...(ed.fields.length ? { fields: cleanFields(ed.fields) } : {}),
        text_sections: ed.sections,
      };
      const created = await createSearch(projectId, payload);
      storeSearchId(projectId, created.id);
      // the new corpus becomes the current one; the ?new=1 flag is consumed (not kept in the URL).
      const params = new URLSearchParams(sp.toString());
      params.set("search", created.id);
      params.delete("new");
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname);
      notifySearchesChanged();
      setCreateOpen(false);
      setCreateQ(null);
      setCreateName(null);
      setCreateSelection({});
    } catch (err) {
      setError(errText(err));
    } finally {
      setCreating(false);
    }
  }

  /** Edit a corpus (spec R3): question + fields + sections → PATCH ?search=<sid>. */
  async function handleSaveCorpus(e: React.SyntheticEvent) {
    e.preventDefault();
    if (!corpusId) return;
    const ed = createQ ?? q;
    if (!ed) return;
    setSaving(true);
    setError(null);
    try {
      await updateProject(
        projectId,
        {
          question: ed.question.trim() || undefined,
          fields: ed.fields.length ? cleanFields(ed.fields) : undefined,
          text_sections: ed.sections,
        },
        corpusId
      );
      setEditOpen(false);
      setCreateQ(null);
      notifySearchesChanged();
    } catch (err) {
      // 409 question_locked (legacy is_default) → the banner keeps its message.
      setError(errText(err));
    } finally {
      setSaving(false);
    }
  }

  /** Launch an analysis of the current corpus (named output, overwrite, or chain). */
  async function handleLaunch(e: React.SyntheticEvent) {
    e.preventDefault();
    if (!corpusId || !launchModel) return;
    const shownName = runName ?? nextAutoName((jobs ?? []).map((j) => j.name));
    setSubmitting(true);
    setError(null);
    try {
      if (chain) {
        // Chained: a new corpus scheduled on this lot, started when the current run finishes.
        await createSearch(projectId, {
          kind: "standard",
          schedule: {
            model: launchModel,
            ...(instructions.trim() ? { instructions: instructions.trim() } : {}),
          },
          selection: { from_search_id: corpusId },
        });
        setChain(false);
        notifySearchesChanged();
        return;
      }
      const payload: Parameters<typeof triggerInference>[1] = {
        model: launchModel,
        search_id: corpusId,
        ...(instructions.trim() ? { instructions: instructions.trim() } : {}),
        ...goldOptions(),
      };
      if (outMode === "overwrite" && overwriteId) payload.overwrite_job_id = overwriteId;
      else if (shownName.trim()) payload.name = shownName.trim();
      await triggerInference(projectId, payload);
      setOutMode("new");
      setOverwriteId("");
      setRunName(null);
      setLaunchOpen(false);
      notifySearchesChanged();
      reloadJobs();
      loadBusy();
    } catch (err) {
      // 409: another job is already running on the project (one active job per project).
      setError(errText(err));
    } finally {
      setSubmitting(false);
    }
  }

  const workersBusy = !!busy && busy.search_id && busy.search_id !== corpusId;
  const chainable = !!busy && !!current && runActive(busy) && (current.predicted ?? 0) < (current.lot_size ?? 0);

  // The creation panel (spec R4): reachable in every state — including S1, where the
  // project's first corpus is born.
  const shownCreateName = createName ?? nextAutoName((searches ?? []).map((s) => s.name));
  const editQ = createQ ?? q;
  const createPanel = isLead && createOpen ? (
    <Card title={t("newCorpusTitle")}>
      <form onSubmit={(e) => void handleCreateCorpus(e)} className="flex flex-col gap-4">
        <Field label={tLead("nameLabel")} hint={tLead("namePlaceholder")}>
          <Input value={shownCreateName} onChange={(e) => setCreateName(e.target.value)} />
        </Field>
        <QuestionEditor
          question={editQ?.question ?? ""}
          fields={editQ?.fields ?? []}
          sections={editQ?.sections ?? ["methods"]}
          onChange={setCreateQ}
        />
        <ArticleSelector projectId={projectId} searches={searches ?? []} value={createSelection} onChange={setCreateSelection} onCount={setCount} />
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" loading={creating} disabled={count === 0}>
            {t("createCorpus")}
          </Button>
          <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>
            {tc("cancel")}
          </Button>
        </div>
      </form>
    </Card>
  ) : null;

  // S1 (spec R2): a corpus-less project never calls search-scoped APIs. The role (project
  // list) and the article count (overview) land in the same commit: no CTA flash for members.
  if (searches !== null && searches.length === 0) {
    if (articleCount === null) return <Skeleton rows={4} />;
    return (
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
          <p className="mt-1 text-sm text-muted">{t("intro")}</p>
        </div>
        {articleCount === 0 ? (
          <EmptyState
            title={tp("noCorpus")}
            action={
              isLead ? (
                <Link href={`/projects/${projectId}/import`}>
                  <Button variant="secondary">{tp("importCta")}</Button>
                </Link>
              ) : undefined
            }
          />
        ) : (
          <CorporaEmpty projectId={projectId} articleCount={articleCount} isLead={isLead} onNew={isLead ? () => setCreateOpen(true) : undefined} />
        )}
        {createPanel}
      </div>
    );
  }

  if (!current || jobs === null) return <Skeleton rows={4} />;

  const shownRunName = runName ?? nextAutoName(jobs.map((j) => j.name));
  const runLabel = (j: InferenceJob) =>
    `${j.name ?? t("runUnnamed")} · ${j.model_used} · ${j.n_predictions ?? num(j.result?.done)}/${num(j.result?.total) || current.lot_size}`;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("intro")}</p>
      </div>

      {error && <ErrorBanner message={error} />}

      {/* The selected corpus: identity + question + edit (spec R3). */}
      <Card
        title={
          <span className="flex flex-wrap items-center justify-between gap-2">
            <span className="flex flex-wrap items-center gap-2">
              {t("corpusCard", { name: current.name })}
              {current.last_job == null && <Badge color="yellow">{t("badgeDraft")}</Badge>}
            </span>
            {isLead && (
              <Button variant="ghost" size="sm" onClick={() => setEditOpen(true)}>
                {t("editQuestion")}
              </Button>
            )}
          </span>
        }
      >
        <div className="flex flex-col gap-1">
          {current.question && (
            <p className="text-sm text-foreground">
              <span className="mr-1 text-xs font-medium text-muted">{tq("questionLabel")} : </span>
              <span>{current.question}</span>
            </p>
          )}
          {(current.fields?.length ?? 0) > 0 && (
            <p className="text-sm text-foreground">
              <span className="mr-1 text-xs font-medium text-muted">{tLead("fieldsLabel")} : </span>
              <span>{current.fields!.map((f) => f.label || f.key).join(" · ")}</span>
            </p>
          )}
        </div>
      </Card>

      {/* The corpus's runs, newest first (spec R3). */}
      <Card title={tLead("title")}>
        {jobs.length === 0 ? (
          <EmptyState
            title={t("runsEmpty")}
            hint={t("runsEmptyHint", { n: current.lot_size })}
            action={
              isLead ? (
                <Button variant="secondary" size="sm" onClick={() => setLaunchOpen(true)}>
                  {t("runsEmptyCta")}
                </Button>
              ) : undefined
            }
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {jobs.map((j) => (
              <RunRow
                key={j.id}
                j={j}
                models={models}
                lot={current.lot_size}
                locale={locale}
                isLead={isLead}
                seeResultsHref={`${withSearch(`/projects/${projectId}/articles`, current.id)}&run=${j.id}`}
                onChat={() => {
                  setChatRequest((p) => ({ searchId: current.id, nonce: (p?.nonce ?? 0) + 1 }));
                  setChatOpen(true);
                  smoothScrollIntoView(chatRef.current);
                }}
                submitting={submitting}
                onOverwrite={(model) => void handleOverwrite(j, model)}
                onNamed={() => {
                  setLaunchModel(j.model_used);
                  setLaunchOpen(true);
                }}
                onDelete={() => setDeleteJob(j)}
                onStop={() => setStopOpen(true)}
                onResume={() => void handleResume(j)}
              />
            ))}
          </ul>
        )}
      </Card>

      {/* Launch an analysis of the corpus (spec R3): named output / overwrite / chain.
          Shown once the corpus has runs, or the project is busy (chain box + the
          « another analysis runs » hint need it), or the user opened it. */}
      {isLead && (jobs.length > 0 || launchOpen || busyActive) && (
        <Card
          title={
            <span className="flex items-center gap-2">
              {t("panelTitle")}
              {launchOpen && (
                <Button type="button" variant="ghost" size="sm" onClick={() => setLaunchOpen(false)}>
                  −
                </Button>
              )}
            </span>
          }
        >
          <form onSubmit={(e) => void handleLaunch(e)} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <p className="text-sm font-semibold text-foreground">{t("stage2")}</p>
              <Select value={launchModel} onChange={(e) => setLaunchModel(e.target.value)} aria-label={t("stage2")}>
                {models.map((m) => (
                  <option key={m.id} value={m.name}>
                    {m.name}
                    {m.ram_usage_mb ? ` · ~${m.ram_usage_mb} MB` : ""}
                  </option>
                ))}
              </Select>
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
              <div className="rounded-lg p-3">
                <Checkbox label={t("chainLabel", { name: current.name })} checked={chain} onChange={(e) => setChain(e.target.checked)} />
                <p className="mt-1 text-xs text-muted">{t("chainHint")}</p>
              </div>
            )}
            <fieldset className="flex flex-col gap-3 rounded-lg border border-border p-3">
              <legend className="px-1 text-sm font-semibold text-foreground">{t("outTitle")}</legend>
              <label className="flex items-center gap-2 text-sm text-foreground">
                <input type="radio" name="out" checked={outMode === "new"} onChange={() => setOutMode("new")} className="h-4 w-4 rounded border-border" />
                {t("outNew")}
              </label>
              {outMode === "new" && (
                <div className="flex flex-col gap-1 pl-6">
                  <p className="text-xs text-muted">{t("outNewHint")}</p>
                  <Input value={shownRunName} onChange={(e) => setRunName(e.target.value)} />
                </div>
              )}
              <label className="flex items-center gap-2 text-sm text-foreground">
                <input type="radio" name="out" checked={outMode === "overwrite"} onChange={() => setOutMode("overwrite")} className="h-4 w-4 rounded border-border" />
                {t("outOverwrite")}
              </label>
              {outMode === "overwrite" && (
                <div className="flex flex-col gap-1 pl-6">
                  <p className="text-xs text-muted">{t("outOverwriteHint")}</p>
                  <Field label={t("outOverwrite")}>
                    <Select value={overwriteId} onChange={(e) => setOverwriteId(e.target.value)}>
                      {jobs.map((j) => (
                        <option key={j.id} value={j.id} disabled={runActive(j)}>
                          {runLabel(j)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
              )}
            </fieldset>
            <div className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" loading={submitting}>
                  {t("run")}
                </Button>
                <Button type="button" variant="secondary" onClick={() => setLaunchOpen(false)}>
                  {t("cancel")}
                </Button>
              </div>
              {workersBusy && busy && (
                <p className="text-xs text-warning">
                  {t("workersBusy", { name: searches?.find((s) => s.id === busy.search_id)?.name ?? "…" })}
                </p>
              )}
            </div>
          </form>
        </Card>
      )}

      {/* Create a corpus of questions (spec R4): the panel also renders in the S1 state. */}
      {createPanel}

      {/* Edit the corpus (spec R3): the same editor, saved through PATCH ?search=. */}
      {isLead && editOpen && editQ && (
        <Card title={t("newCorpusTitle")}>
          <form onSubmit={(e) => void handleSaveCorpus(e)} className="flex flex-col gap-4">
            <QuestionEditor
              question={editQ.question}
              fields={editQ.fields}
              sections={editQ.sections}
              onChange={setCreateQ}
            />
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" loading={saving}>
                {tc("save")}
              </Button>
              <Button type="button" variant="secondary" onClick={() => {
                setEditOpen(false);
                setCreateQ(null);
              }}>
                {tc("cancel")}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {/* Stopping the active run confirms through the Modal (spec clarte-3 R8). */}
      {stopOpen && (
        <Modal title={t("stop")} onClose={() => setStopOpen(false)}>
          <div className="flex flex-col gap-4">
            <p className="text-sm text-muted">{t("stopConfirm")}</p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setStopOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button variant="danger" size="sm" loading={submitting} onClick={() => void confirmStop()}>
                {t("stop")}
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {/* Delete a run and its results (LEAD). */}
      {deleteJob && (
        <Modal title={tc("delete")} onClose={() => setDeleteJob(null)}>
          <div className="flex flex-col gap-4">
            <p className="text-sm text-muted">{t("runDeleteConfirm", { name: deleteJob.name ?? t("runUnnamed") })}</p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setDeleteJob(null)}>
                {tc("cancel")}
              </Button>
              <Button variant="danger" size="sm" loading={submitting} onClick={() => void handleDeleteJob()}>
                {tc("delete")}
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {/* Chat RAG (spec 2026-11-07-chat-rag §5) : visible aux membres — interroger
          tout le corpus du projet, ou le lot d'une recherche. Disclosure repliée
          par défaut (spec clarte-3 R3) : la page garde une seule intention. */}
      <details
        ref={chatRef}
        className="scroll-mt-4 rounded-xl bg-surface p-4 shadow-sm"
        open={chatOpen}
        onToggle={(e) => setChatOpen(e.currentTarget.open)}
      >
        <summary className="cursor-pointer select-none text-base font-semibold text-foreground">
          {t("chatTitle")}
        </summary>
        <div className="mt-3">
          <ChatRagPanel
            projectId={projectId}
            models={models}
            isLead={isLead}
            searches={(searches ?? []).filter((s) => s.kind === "standard")}
            resultsRequest={chatRequest}
          />
        </div>
      </details>
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
