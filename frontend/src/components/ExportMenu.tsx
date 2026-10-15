"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  crateDownloadUrl,
  CrateExport,
  exportUrl,
  getCrateExport,
  referencesUrl,
  startCrateExport,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { useSearchId } from "@/lib/useSearch";

/**
 * Exports de la recherche courante : Excel, références (RIS / BibTeX, suivent les filtres de la liste) et
 * corpus complet RO-Crate (tâche de fond : suivi ici + e-mail à la fin).
 */
export function ExportMenu({
  projectId,
  status,
  answers,
}: {
  projectId: string;
  status?: string;
  answers?: Record<string, string>;
}) {
  const t = useTranslations("articles");
  const searchId = useSearchId(projectId);
  const [job, setJob] = useState<CrateExport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const filtered = Boolean(status) || Object.values(answers ?? {}).some((v) => v !== "");

  const poll = useCallback(async () => {
    try {
      const j = await getCrateExport(projectId, searchId);
      setJob(j);
      if (j && (j.status === "PENDING" || j.status === "RUNNING")) timer.current = setTimeout(poll, 3000);
    } catch {
      /* réessaie à la prochaine visite */
    }
  }, [projectId, searchId]);

  useEffect(() => {
    void poll();
    return () => clearTimeout(timer.current);
  }, [poll]);

  async function start() {
    setError(null);
    try {
      setJob(await startCrateExport(projectId, searchId));
      clearTimeout(timer.current);
      timer.current = setTimeout(poll, 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }

  const busy = job?.status === "PENDING" || job?.status === "RUNNING";
  const r = job?.result ?? {};
  const mb = r.size ? (r.size / 1048576).toFixed(1) : "?";

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <a href={exportUrl(projectId, searchId)} download>
          <Button variant="secondary">⬇ {t("exportXlsx")}</Button>
        </a>
        <a href={referencesUrl(projectId, "ris", status, answers, searchId)} download>
          <Button variant="secondary" title={t("exportRefsHelp")}>⬇ {t("exportRis")}</Button>
        </a>
        <a href={referencesUrl(projectId, "bib", status, answers, searchId)} download>
          <Button variant="secondary">⬇ BibTeX</Button>
        </a>
        <Button variant="secondary" onClick={start} disabled={busy} title={t("exportZipHelp")}>
          📦 {t("exportZip")}
        </Button>
      </div>
      {filtered && <p className="text-xs text-muted">{t("exportRefsFiltered")}</p>}
      {busy && (
        <p className="text-xs text-muted" role="status">
          {t("exportZipRunning", { done: r.done ?? 0, total: r.total ?? 0 })} {t("exportZipMail")}
        </p>
      )}
      {job?.status === "COMPLETED" && job.download && (
        <a href={crateDownloadUrl(projectId, job.id)} download className="text-xs text-primary hover:underline">
          ⬇ {t("exportZipReady", { mb, articles: r.articles ?? 0, pdf: r.pdf ?? 0 })}
        </a>
      )}
      {job?.status === "FAILED" && <p className="text-xs text-danger">{t("exportZipFailed")}</p>}
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  );
}
