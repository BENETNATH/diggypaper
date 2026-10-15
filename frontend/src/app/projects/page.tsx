"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { listProjects, createProject, Project } from "@/lib/api";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { Skeleton, ErrorBanner, EmptyState } from "@/components/ui/Feedback";

function ProjectsContent() {
  const t = useTranslations("projects");
  const { user } = useAuth();
  const canCreate = user?.can_create_projects ?? false;
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [language, setLanguage] = useState("en");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setProjects(await listProjects());
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const project = await createProject({
        title,
        description: description || undefined,
        default_language: language,
      });
      window.location.href = `/projects/${project.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
      setCreating(false);
    }
  }

  if (error && !projects) {
    return <ErrorBanner message={error} onRetry={() => void load()} />;
  }
  if (!projects) {
    return <Skeleton rows={3} />;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        {canCreate && <Button onClick={() => setShowForm((v) => !v)}>{t("newProject")}</Button>}
      </div>

      {canCreate && showForm && (
        <Card title={t("newProject")}>
          <form onSubmit={handleCreate} className="flex flex-col gap-4">
            <Field label={t("titleLabel")}>
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
                minLength={1}
              />
            </Field>
            <Field label={t("descriptionLabel")}>
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </Field>
            <Field label={t("languageLabel")}>
              <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
                <option value="fr">Français</option>
                <option value="en">English</option>
                <option value="de">Deutsch</option>
                <option value="es">Español</option>
              </Select>
            </Field>
            <div className="flex gap-2">
              <Button type="submit" loading={creating}>
                {t("create")}
              </Button>
              <Button type="button" variant="secondary" onClick={() => setShowForm(false)}>
                {t("cancel")}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {projects.length === 0 ? (
        <EmptyState title={t("empty")} hint={t("emptyHint")} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {projects.map((p) => (
            <Link key={p.id} href={`/projects/${p.id}`} className="block">
              <Card className="transition-shadow hover:shadow-md">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="font-semibold text-foreground">{p.title}</h3>
                    {p.description && (
                      <p className="mt-1 text-sm text-muted">{p.description}</p>
                    )}
                  </div>
                  <Badge color={statusColor(p.status)}>{p.status}</Badge>
                </div>
                <div className="mt-3 flex items-center gap-3 text-sm text-muted">
                  <span>{t("articles")}: {p.article_count ?? 0}</span>
                  {p.role_in_project && <span>· {p.role_in_project}</span>}
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ProjectsPage() {
  return (
    <RequireAuth>
      <ProjectsContent />
    </RequireAuth>
  );
}
