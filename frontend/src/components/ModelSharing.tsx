"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { addModelShare, listModelShares, listProjects, removeModelShare, ModelShare, Project } from "@/lib/api";
import { useApiError } from "@/lib/errorMsg";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";
import { Badge } from "@/components/ui/Card";

/** Who can use this model: users (by e-mail) and projects (their LEAD). */
export function ModelSharing({ modelId }: { modelId: string }) {
  const t = useTranslations("models");
  const toError = useApiError();
  const [shares, setShares] = useState<ModelShare[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [email, setEmail] = useState("");
  const [projectId, setProjectId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    return listModelShares(modelId)
      .then(setShares)
      .catch((e) => setError(toError(e)));
  }, [modelId, toError]);

  useEffect(() => {
    load();
    listProjects().then(setProjects).catch(() => {});
  }, [load]);

  async function add(target: { email?: string; project_id?: string }) {
    setError(null);
    try {
      await addModelShare(modelId, target);
      setEmail("");
      setProjectId("");
      await load();
    } catch (e) {
      setError(toError(e));
    }
  }

  async function remove(id: string) {
    setError(null);
    try {
      await removeModelShare(modelId, id);
      await load();
    } catch (e) {
      setError(toError(e));
    }
  }

  return (
    <div className="mt-2 rounded-lg bg-surface-muted p-3 text-sm">
      <p className="mb-2 text-xs text-muted">{t("shareHint")}</p>
      {shares === null ? (
        <p className="text-xs text-muted">…</p>
      ) : shares.length === 0 ? (
        <p className="text-xs text-muted">{t("shareNone")}</p>
      ) : (
        <ul className="mb-2 flex flex-wrap gap-2">
          {shares.map((s) => (
            <li key={s.id} className="flex items-center gap-1">
              <Badge color={s.type === "project" ? "blue" : "gray"}>
                {s.type === "project" ? t("shareProjectLabel") : t("shareUserLabel")} {s.label}
              </Badge>
              <Button size="sm" variant="ghost" onClick={() => void remove(s.id)}>
                {t("shareRemove")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder={t("shareEmail")}
          className="w-56"
        />
        <Button size="sm" variant="secondary" disabled={!email.trim()} onClick={() => void add({ email: email.trim() })}>
          {t("shareUser")}
        </Button>
        <Select value={projectId} onChange={(e) => setProjectId(e.target.value)} className="w-56">
          <option value="">{t("shareProject")}</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title}
            </option>
          ))}
        </Select>
        <Button size="sm" variant="secondary" disabled={!projectId} onClick={() => void add({ project_id: projectId })}>
          {t("shareProjectBtn")}
        </Button>
      </div>
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </div>
  );
}
