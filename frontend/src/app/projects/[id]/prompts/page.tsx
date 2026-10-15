"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  listPrompts,
  createPrompt,
  updatePrompt,
  PromptTemplate,
} from "@/lib/api";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { Skeleton, ErrorBanner, EmptyState } from "@/components/ui/Feedback";

function PromptsContent() {
  const t = useTranslations("prompts");
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  const [prompts, setPrompts] = useState<PromptTemplate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<PromptTemplate | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);

  const [version, setVersion] = useState("v1.0");
  const [title, setTitle] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [userSchema, setUserSchema] = useState("");

  const load = useCallback(async () => {
    setError(null);
    try {
      setPrompts(await listPrompts(projectId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  function startCreate() {
    setEditing(null);
    setVersion("v1.0");
    setTitle("");
    setSystemPrompt("");
    setUserSchema("");
    setShowForm(true);
  }

  function startEdit(p: PromptTemplate) {
    setEditing(p);
    setVersion(p.version_number);
    setTitle(p.title ?? "");
    setSystemPrompt(p.system_prompt ?? "");
    setUserSchema(p.user_prompt_schema ?? "");
    setShowForm(true);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      if (editing) {
        await updatePrompt(projectId, editing.id, {
          title,
          system_prompt: systemPrompt,
          user_prompt_schema: userSchema,
        });
      } else {
        await createPrompt(projectId, {
          version_number: version,
          title,
          system_prompt: systemPrompt,
          user_prompt_schema: userSchema,
        });
      }
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleValidate(p: PromptTemplate) {
    setError(null);
    try {
      await updatePrompt(projectId, p.id, { status: "VALIDATED" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    }
  }

  if (error && !prompts) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!prompts) return <Skeleton rows={3} />;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <Button onClick={startCreate}>{t("new")}</Button>
      </div>

      {error && <ErrorBanner message={error} />}

      {showForm && (
        <Card title={editing ? t("edit") : t("new")}>
          <form onSubmit={handleSave} className="flex flex-col gap-4">
            {!editing && (
              <Field label={t("version")}>
                <Input value={version} onChange={(e) => setVersion(e.target.value)} required />
              </Field>
            )}
            <Field label={t("title")}>
              <Input value={title} onChange={(e) => setTitle(e.target.value)} />
            </Field>
            <Field label={t("systemPrompt")}>
              <Textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={6}
              />
            </Field>
            <Field label={t("userSchema")}>
              <Textarea
                value={userSchema}
                onChange={(e) => setUserSchema(e.target.value)}
                rows={4}
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

      {prompts.length === 0 ? (
        <EmptyState title={t("empty")} hint={t("emptyHint")} />
      ) : (
        <div className="flex flex-col gap-3">
          {prompts.map((p) => (
            <Card key={p.id}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-semibold text-foreground">
                      {p.version_number}
                    </span>
                    {p.title && <span className="text-sm text-muted">{p.title}</span>}
                    <Badge color={statusColor(p.status)}>{p.status}</Badge>
                  </div>
                  {p.system_prompt && (
                    <p className="mt-2 line-clamp-2 text-sm text-muted">
                      {p.system_prompt}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 gap-2">
                  {p.status === "DRAFT" && (
                    <Button variant="secondary" onClick={() => handleValidate(p)}>
                      {t("validate")}
                    </Button>
                  )}
                  <Button variant="ghost" onClick={() => startEdit(p)}>
                    {t("edit")}
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

export default function PromptsPage() {
  return (
    <RequireAuth>
      <PromptsContent />
    </RequireAuth>
  );
}
