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
import { Menu, MenuItem, MenuNote, MenuSeparator } from "@/components/ui/Menu";
import { useSearchId } from "@/lib/useSearch";

/**
 * Exports de la recherche courante : Excel, références (RIS / BibTeX, suivent les filtres de la liste) et
 * corpus complet RO-Crate (tâche de fond : suivi ici + e-mail à la fin).
 */
export function ExportMenu({
  projectId,
  status,
  answers,
  text,
  fulltext,
}: {
  projectId: string;
  status?: string;
  answers?: Record<string, string>;
  text?: string;
  fulltext?: boolean;
}) {
  const t = useTranslations("articles");
  const searchId = useSearchId(projectId);
  const [job, setJob] = useState<CrateExport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const filtered = Boolean(status) || fulltext != null || Boolean(text?.trim()) || Object.values(answers ?? {}).some((v) => v !== "");

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
    <Menu label={t("exportMenu")} busy={busy} closeOnClick={false}>
      <MenuItem href={exportUrl(projectId, searchId)} download>
        ⬇ {t("exportXlsx")}
      </MenuItem>
      <MenuItem href={referencesUrl(projectId, "ris", status, answers, searchId, text, fulltext)} download>
        ⬇ {t("exportRis")}
      </MenuItem>
      <MenuItem href={referencesUrl(projectId, "bib", status, answers, searchId, text, fulltext)} download>
        ⬇ BibTeX
      </MenuItem>
      {filtered && <MenuNote>{t("exportRefsFiltered")}</MenuNote>}
      <MenuSeparator />
      <MenuItem onClick={() => void start()} disabled={busy}>
        📦 {t("exportZip")}
      </MenuItem>
      <MenuNote>{t("exportZipHelp")}</MenuNote>
      {busy && (
        <MenuNote>
          <span role="status">
            {t("exportZipRunning", { done: r.done ?? 0, total: r.total ?? 0 })} {t("exportZipMail")}
          </span>
        </MenuNote>
      )}
      {job?.status === "COMPLETED" && job.download && (
        <MenuItem href={crateDownloadUrl(projectId, job.id)} download>
          <span className="text-primary">⬇ {t("exportZipReady", { mb, articles: r.articles ?? 0, pdf: r.pdf ?? 0 })}</span>
        </MenuItem>
      )}
      {job?.status === "FAILED" && <MenuNote><span className="text-danger">{t("exportZipFailed")}</span></MenuNote>}
      {error && <MenuNote><span className="text-danger">{error}</span></MenuNote>}
    </Menu>
  );
}
