"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  ApiError,
  createSearch,
  generatePrompt,
  isScorer,
  listGold,
  triggerBenchmark,
  ModelInfo,
  SearchInfo,
  Selection,
} from "@/lib/api";
import {
  benchmarkErrorKey,
  DEFAULT_TUNING_N,
  estimateCalls,
  LOT_WARN,
  MAX_CALLS,
  MAX_GOLD_EXAMPLES,
  MAX_VARIANTS,
  nextVariantName,
  toVariants,
  tooManyCalls,
  VariantDraft,
  variantsProblem,
} from "@/lib/benchmark";
import { notifySearchesChanged } from "@/lib/useSearch";
import { ArticleSelector } from "@/components/ArticleSelector";
import { Button } from "@/components/ui/Button";
import { Badge, Card } from "@/components/ui/Card";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/Field";

const newSeed = (): number => Math.floor(Math.random() * 1e9);

/**
 * Formulaire « Nouveau benchmark » : banc d'essai (lot d'une recherche, idéalement un sous-lot de
 * mise au point), modèles, variantes de prompt, options de gold, estimation du nombre d'appels.
 */
export function BenchmarkForm({
  projectId,
  searches,
  models,
  defaultSearchId,
  onLaunched,
  onCancel,
}: {
  projectId: string;
  searches: SearchInfo[];
  models: ModelInfo[];
  defaultSearchId?: string;
  onLaunched: () => void;
  onCancel: () => void;
}) {
  const t = useTranslations("benchmarks");
  const ts = useTranslations("searches");

  const initial = searches.find((s) => s.is_default) ?? searches[0];
  const [searchId, setSearchId] = useState<string>(
    // Une recherche de mise au point existante est le banc d'essai naturel ; sinon la recherche courante.
    () => searches.find((s) => s.kind === "tuning")?.id ?? defaultSearchId ?? initial?.id ?? ""
  );
  const bench = searches.find((s) => s.id === searchId);
  const lot = bench?.lot_size ?? 0;

  // Sous-lot de mise au point.
  const [tuning, setTuning] = useState(false);
  const [sel, setSel] = useState<Selection>({});
  const [selCount, setSelCount] = useState<number | null>(null);
  const [tuningName, setTuningName] = useState("");
  const [creating, setCreating] = useState(false);

  const [selected, setSelected] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<VariantDraft[]>([]);
  const [fetchingPrompt, setFetchingPrompt] = useState(false);

  const [goldCount, setGoldCount] = useState<number | null>(null);
  const [useGold, setUseGold] = useState(false);
  const [examples, setExamples] = useState(false);
  const [exampleN, setExampleN] = useState(5);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Références du gold de la recherche choisie (celles du lot seules comptent, le serveur filtre).
  useEffect(() => {
    if (!searchId) return;
    let stale = false;
    setGoldCount(null);
    listGold(projectId, searchId)
      .then((g) => !stale && setGoldCount(g.length))
      .catch(() => !stale && setGoldCount(null));
    return () => {
      stale = true;
    };
  }, [projectId, searchId]);

  const noGold = goldCount === 0;
  useEffect(() => {
    if (noGold) {
      setUseGold(false);
      setExamples(false);
    }
  }, [noGold]);

  const scorers = useMemo(
    () => selected.map((n) => models.find((m) => m.name === n)).map((m) => (m ? isScorer(m) : false)),
    [selected, models]
  );
  const calls = estimateCalls(lot, scorers, drafts.length);
  const problem = variantsProblem(drafts);
  const tooMany = calls > MAX_CALLS;
  const canRun = selected.length > 0 && lot > 0 && !problem && !tooMany && !submitting;

  const toggleModel = (name: string) =>
    setSelected((prev) => (prev.includes(name) ? prev.filter((m) => m !== name) : [...prev, name]));
  const patchDraft = (i: number, patch: Partial<VariantDraft>) =>
    setDrafts((ds) => ds.map((d, j) => (j === i ? { ...d, ...patch } : d)));

  function message(e: unknown): string {
    const detail = e instanceof ApiError ? e.detail : e instanceof Error ? e.message : "error";
    const key = benchmarkErrorKey(detail);
    if (!key) return detail;
    return key === "err_tooMany" ? t(key, { n: tooManyCalls(detail) ?? calls, max: MAX_CALLS }) : t(key);
  }

  function startTuning() {
    // On tire dans le lot d'une recherche ordinaire (jamais dans un autre sous-lot) ; modifiable ci-dessous.
    const origin = bench && bench.kind === "standard" && !bench.is_default ? bench.id : undefined;
    setSel({ from_search_id: origin, sample_n: DEFAULT_TUNING_N, seed: newSeed() });
    setTuningName(t("tuningDefaultName", { date: new Date().toLocaleDateString() }));
    setTuning(true);
  }

  async function createTuning() {
    setCreating(true);
    setError(null);
    try {
      const s = await createSearch(projectId, { name: tuningName.trim() || undefined, kind: "tuning", selection: sel });
      notifySearchesChanged();
      setSearchId(s.id);
      setTuning(false);
    } catch (e) {
      setError(message(e));
    } finally {
      setCreating(false);
    }
  }

  async function fromGenerated() {
    setFetchingPrompt(true);
    setError(null);
    try {
      const p = await generatePrompt(projectId, searchId || undefined);
      const draft: VariantDraft = {
        name: t("generatedName"),
        system_prompt: p.system_prompt ?? "",
        ...(p.user_prompt_schema ? { user_prompt_schema: p.user_prompt_schema } : {}),
      };
      setDrafts((ds) => {
        const blank = ds.length === 1 && !ds[0].system_prompt.trim();
        if (blank) return [{ ...draft, name: ds[0].name.trim() || draft.name }];
        if (ds.length >= MAX_VARIANTS) return ds;
        return [...ds, { ...draft, name: nextVariantName(draft.name, ds.map((d) => d.name)) }];
      });
    } catch (e) {
      setError(message(e));
    } finally {
      setFetchingPrompt(false);
    }
  }

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    if (!canRun) return;
    setSubmitting(true);
    setError(null);
    try {
      await triggerBenchmark(projectId, {
        models: selected,
        ...(searchId ? { search_id: searchId } : {}),
        ...(drafts.length ? { variants: toVariants(drafts) } : {}),
        use_gold: useGold,
        gold_examples: examples ? exampleN : 0,
      });
      onLaunched();
    } catch (err) {
      setError(message(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card title={t("new")}>
      <p className="mb-4 text-sm text-muted">{t("concept")}</p>
      <form
        onSubmit={handleRun}
        // Entrée dans un champ (nom, filtres du sous-lot) ne doit pas lancer le benchmark.
        onKeyDown={(e) => {
          if (e.key === "Enter" && e.target instanceof HTMLInputElement) e.preventDefault();
        }}
        className="flex flex-col gap-6"
      >
        {/* 1. Banc d'essai */}
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-foreground">1. {t("benchTitle")}</h3>
          <p className="text-xs text-muted">{t("benchHint")}</p>
          <Select value={searchId} onChange={(e) => setSearchId(e.target.value)} aria-label={t("benchTitle")}>
            {searches.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.kind === "tuning" ? ` (${ts("kind_tuning")})` : ""} — {s.lot_size} {ts("articlesUnit")}
              </option>
            ))}
          </Select>
          {bench?.kind !== "tuning" && <p className="text-xs text-warning">{t("notTuning")}</p>}
          {lot > LOT_WARN && <p className="rounded-lg bg-warning/10 p-2 text-sm text-foreground">{t("lotWarn", { n: lot })}</p>}
          {!tuning ? (
            <div>
              <Button type="button" variant="secondary" size="sm" onClick={startTuning}>
                {t("tuningCreate")}
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-3 rounded-lg border border-border p-3">
              <p className="text-sm font-medium text-foreground">{t("tuningTitle")}</p>
              <p className="text-xs text-muted">{t("tuningHint")}</p>
              <Field label={t("tuningName")}>
                <Input value={tuningName} onChange={(e) => setTuningName(e.target.value)} />
              </Field>
              <ArticleSelector projectId={projectId} searches={searches} value={sel} onChange={setSel} onCount={setSelCount} />
              <div className="flex gap-2">
                <Button
                  type="button"
                  loading={creating}
                  disabled={!selCount}
                  onClick={() => void createTuning()}
                >
                  {t("tuningSubmit")}
                </Button>
                <Button type="button" variant="secondary" onClick={() => setTuning(false)}>
                  {t("cancel")}
                </Button>
              </div>
            </div>
          )}
        </section>

        {/* 2. Modèles */}
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-foreground">2. {t("models")}</h3>
          {models.length === 0 && (
            <p className="rounded-lg bg-warning/10 p-3 text-sm text-foreground">
              {t("noModels")}{" "}
              <Link href="/models" className="font-medium text-primary hover:underline">
                {t("manageModels")} →
              </Link>
            </p>
          )}
          <div className="grid gap-2 sm:grid-cols-2">
            {models.map((m) => (
              <Checkbox
                key={m.id}
                checked={selected.includes(m.name)}
                onChange={() => toggleModel(m.name)}
                label={
                  <>
                    {m.name}
                    {isScorer(m) && (
                      <span className="ml-2">
                        <Badge>{t("scorerBadge")}</Badge>
                      </span>
                    )}
                  </>
                }
              />
            ))}
          </div>
          {scorers.includes(true) && drafts.length > 0 && <p className="text-xs text-muted">{t("scorerHint")}</p>}
        </section>

        {/* 3. Variantes de prompt */}
        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-foreground">3. {t("variantsTitle")}</h3>
          <p className="text-xs text-muted">{t("variantsHint")}</p>
          {drafts.length === 0 && <p className="text-sm text-foreground">{t("variantsNone")}</p>}
          {drafts.map((d, i) => (
            <div key={i} className="flex flex-col gap-2 rounded-lg border border-border p-3">
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  <Field label={t("variantName")}>
                    <Input value={d.name} maxLength={100} onChange={(e) => patchDraft(i, { name: e.target.value })} />
                  </Field>
                </div>
                <Button type="button" variant="ghost" size="sm" onClick={() => setDrafts((ds) => ds.filter((_, j) => j !== i))}>
                  {t("variantRemove")}
                </Button>
              </div>
              <Field label={t("variantPrompt")}>
                <Textarea
                  rows={6}
                  value={d.system_prompt}
                  placeholder={t("variantPromptPlaceholder")}
                  onChange={(e) => patchDraft(i, { system_prompt: e.target.value })}
                />
              </Field>
            </div>
          ))}
          {problem && drafts.length > 0 && (
            <p className="text-sm text-danger" role="alert">
              {t(`variantErr_${problem}`)}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={drafts.length >= MAX_VARIANTS}
              onClick={() =>
                setDrafts((ds) => [...ds, { name: nextVariantName(t("variantDefault"), ds.map((d) => d.name)), system_prompt: "" }])
              }
            >
              + {t("variantAdd")}
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={fetchingPrompt}
              disabled={drafts.length >= MAX_VARIANTS}
              onClick={() => void fromGenerated()}
            >
              {t("variantFromGenerated")}
            </Button>
          </div>
          <p className="text-xs text-muted">{t("variantsMax", { max: MAX_VARIANTS })}</p>
        </section>

        {/* 4. Gold */}
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-foreground">4. {t("goldTitle")}</h3>
          <p className="text-xs text-muted">{t("goldHint")}</p>
          <Checkbox label={t("useGold")} checked={useGold} disabled={noGold} onChange={(e) => setUseGold(e.target.checked)} />
          <div className="flex flex-wrap items-center gap-3">
            <Checkbox
              label={t("useExamples")}
              checked={examples}
              disabled={noGold}
              onChange={(e) => setExamples(e.target.checked)}
            />
            {examples && (
              <label className="flex items-center gap-2 text-sm text-foreground">
                N =
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
          <p className="text-xs text-muted">
            {noGold ? t("goldNone") : goldCount === null ? "…" : t("goldCount", { n: goldCount })}
          </p>
          {examples && <p className="text-xs text-muted">{t("examplesHint")}</p>}
        </section>

        {/* 5. Estimation et lancement */}
        <section className="flex flex-col gap-2">
          <p className="text-sm text-foreground" role="status">
            <b>{t("estimate", { calls, lot, models: selected.length, variants: Math.max(1, drafts.length) })}</b>
          </p>
          {tooMany && (
            <p className="text-sm text-danger" role="alert">
              {t("err_tooMany", { n: calls, max: MAX_CALLS })}
            </p>
          )}
          {error && (
            <p className="text-sm text-danger" role="alert">
              ⚠ {error}
            </p>
          )}
          <div className="flex gap-2">
            <Button type="submit" loading={submitting} disabled={!canRun}>
              {t("run")}
            </Button>
            <Button type="button" variant="secondary" onClick={onCancel}>
              {t("cancel")}
            </Button>
          </div>
        </section>
      </form>
    </Card>
  );
}
