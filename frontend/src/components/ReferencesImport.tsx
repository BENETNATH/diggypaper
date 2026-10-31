"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { importReferences, ReferencesPreview } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/ui/Feedback";

const MAX_BYTES = 5_000_000;

/** Corpus from a list of references: pasted DOI/PMID or a Zotero file (BibTeX, RIS, CSL JSON, CSV). */
export function ReferencesImport({ projectId, onStarted }: { projectId: string; onStarted: () => void }) {
  const t = useTranslations("import");
  const [content, setContent] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [preview, setPreview] = useState<ReferencesPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function change(text: string, name: string | null) {
    setContent(text);
    setFileName(name);
    setPreview(null);
    setError(null);
  }

  async function pickFile(file: File | undefined) {
    if (!file) return;
    if (file.size > MAX_BYTES) return setError(t("refsTooBig"));
    change(await file.text(), file.name);
  }

  async function analyse() {
    setBusy(true);
    setError(null);
    try {
      setPreview(await importReferences(projectId, content, true));
    } catch {
      setError(t("refsNone"));
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      await importReferences(projectId, content, false);
      change("", null);
      onStarted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted">{t("refsIntro")}</p>
      {error && <ErrorBanner message={error} />}
      {fileName ? (
        <p className="flex items-center gap-2 rounded-lg bg-surface-muted p-3 text-sm">
          <span className="min-w-0 truncate font-medium">{fileName}</span>
          <button type="button" className="text-primary hover:underline" onClick={() => change("", null)}>
            {t("refsClear")}
          </button>
        </p>
      ) : (
        <textarea
          value={content}
          onChange={(e) => change(e.target.value, null)}
          rows={5}
          placeholder={t("refsPlaceholder")}
          className="w-full rounded-lg border border-border bg-surface p-2 font-mono text-xs"
          aria-label={t("refsPlaceholder")}
        />
      )}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="cursor-pointer font-medium text-primary hover:underline">
          {t("refsFile")}
          <input
            type="file"
            accept=".bib,.bibtex,.ris,.json,.csv,.txt,text/plain,application/json"
            className="hidden"
            onChange={(e) => {
              void pickFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </label>
        <span className="text-xs text-muted">{t("refsFormats")}</span>
      </div>
      {preview && (
        <p className="rounded-lg bg-surface-muted p-3 text-sm text-foreground">
          {t("refsFound", {
            count: preview.count,
            format: t(`refsFormat_${preview.format}`),
            doi: preview.with_doi,
            pmid: preview.with_pmid,
            title: preview.title_only,
          })}
          {preview.count > preview.max && (
            <span className="text-warning"> — {t("refsMax", { max: preview.max })}</span>
          )}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="secondary" loading={busy && !preview} disabled={!content.trim() || busy} onClick={analyse}>
          {t("refsAnalyse")}
        </Button>
        <Button type="button" loading={busy && !!preview} disabled={!preview || busy} onClick={start}>
          {t("refsImport")}
        </Button>
      </div>
      <p className="text-xs text-muted">{t("refsHint")}</p>
    </div>
  );
}
