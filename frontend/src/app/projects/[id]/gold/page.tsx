"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  listGold,
  createGold,
  deleteGold,
  listArticles,
  getProject,
  GoldArticle,
  Article,
  SchemaField,
} from "@/lib/api";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Field, Select, Textarea } from "@/components/ui/Field";
import { Card } from "@/components/ui/Card";
import { DecisionFields, defaultValues, Values } from "@/components/DecisionFields";
import { Skeleton, ErrorBanner, EmptyState } from "@/components/ui/Feedback";

function GoldContent() {
  const t = useTranslations("gold");
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  const [gold, setGold] = useState<GoldArticle[] | null>(null);
  const [articles, setArticles] = useState<Article[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);

  // Formulaire ground truth.
  const [articleId, setArticleId] = useState("");
  const [fields, setFields] = useState<SchemaField[]>([]);
  const [values, setValues] = useState<Values>({});
  const [confidence, setConfidence] = useState(0.5);
  const [quotes, setQuotes] = useState("");
  const [reasoning, setReasoning] = useState("");

  const load = useCallback(async () => {
    setError(null);
    try {
      const [g, a, p] = await Promise.all([
        listGold(projectId),
        listArticles(projectId, 1, 200),
        getProject(projectId),
      ]);
      setFields(p.fields ?? []);
      setValues(defaultValues(p.fields ?? []));
      setGold(g);
      setArticles(a.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await createGold(projectId, articleId, {
        ...values,
        confidence_score: confidence,
        evidence_quotes: quotes
          .split("\n")
          .map((q) => q.trim())
          .filter(Boolean),
        reasoning_summary: reasoning,
      });
      setShowForm(false);
      setArticleId("");
      setQuotes("");
      setReasoning("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(goldId: string) {
    setError(null);
    try {
      await deleteGold(projectId, goldId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    }
  }

  if (error && !gold) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!gold) return <Skeleton rows={3} />;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <Button onClick={() => setShowForm((v) => !v)}>{t("add")}</Button>
      </div>

      {error && <ErrorBanner message={error} />}

      {showForm && (
        <Card title={t("add")}>
          <form onSubmit={handleCreate} className="flex flex-col gap-4">
            <Field label={t("article")}>
              <Select value={articleId} onChange={(e) => setArticleId(e.target.value)} required>
                <option value="">{t("selectArticle")}</option>
                {articles.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.title ?? a.pmid ?? a.id}
                  </option>
                ))}
              </Select>
            </Field>
            <DecisionFields fields={fields} values={values} onChange={setValues} />
            <Field label={`${t("confidence")} (${confidence.toFixed(2)})`}>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={confidence}
                onChange={(e) => setConfidence(parseFloat(e.target.value))}
                className="w-full"
              />
            </Field>
            <Field label={t("quotes")} hint={t("quotesHint")}>
              <Textarea
                value={quotes}
                onChange={(e) => setQuotes(e.target.value)}
                rows={3}
              />
            </Field>
            <Field label={t("reasoning")}>
              <Textarea
                value={reasoning}
                onChange={(e) => setReasoning(e.target.value)}
                rows={2}
              />
            </Field>
            <div className="flex gap-2">
              <Button type="submit" loading={saving}>
                {t("save")}
              </Button>
              <Button type="button" variant="secondary" onClick={() => setShowForm(false)}>
                {t("cancel")}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {gold.length === 0 ? (
        <EmptyState title={t("empty")} hint={t("emptyHint")} />
      ) : (
        <Card>
          <ul className="divide-y divide-border">
            {gold.map((g) => (
              <li key={g.id} className="flex items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground">
                    {g.article_title ?? g.article_id}
                  </p>
                  <p className="text-xs text-muted">
                    {fields
                      .map((f) => `${f.label}: ${String(g.ground_truth_json[f.key])}`)
                      .join(" · ")}
                  </p>
                </div>
                <Button variant="danger" onClick={() => handleDelete(g.id)}>
                  {t("delete")}
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

export default function GoldPage() {
  return (
    <RequireAuth>
      <GoldContent />
    </RequireAuth>
  );
}
