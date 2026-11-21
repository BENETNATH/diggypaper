"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { matchArticlePdf } from "@/lib/api";
import { useApiError } from "@/lib/errorMsg";
import { Button } from "@/components/ui/Button";

type Row = {
  name: string;
  state: "pending" | "ok" | "nomatch" | "ambiguous" | "error";
  title?: string | null;
  error?: string;
};

const isPdf = (f: File) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");

const ROW_TONE: Record<Row["state"], string> = {
  pending: "text-muted",
  ok: "text-success",
  nomatch: "text-danger",
  ambiguous: "text-warning",
  error: "text-danger",
};

/**
 * PDF deposit on the article list (spec 2026-11-21-liste-depot-copie R1–R5): the
 * single owner of the deposit logic. `overlay` turns the wrapped card into the
 * drop zone (mockup 01); `inline` is the standalone zone of the BulkPdfUpload
 * modal. Each PDF is matched to the corpus article it belongs to (matchArticlePdf,
 * one call per file, sequential); non-PDFs are ignored silently (a corrupt PDF's
 * 422 stays backend-side). Drag is a shortcut: the hidden file input (button)
 * remains the keyboard / screen-reader entry point (R7).
 */
export function PdfDropzone({
  projectId,
  variant,
  onDone,
  children,
}: {
  projectId: string;
  variant: "overlay" | "inline";
  /** Called once the whole batch is done (successes and failures): the host reloads the list (R4). */
  onDone?: () => void;
  children?: React.ReactNode;
}) {
  const t = useTranslations("articles");
  const tc = useTranslations("common");
  const errText = useApiError();
  const inputRef = useRef<HTMLInputElement>(null);
  const depth = useRef(0);
  const [active, setActive] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);

  async function handleFiles(list: File[]) {
    const pdfs = list.filter(isPdf);
    if (pdfs.length === 0) return;
    setRows(pdfs.map((f) => ({ name: f.name, state: "pending" as const })));
    for (let i = 0; i < pdfs.length; i++) {
      let row: Row;
      try {
        const r = await matchArticlePdf(projectId, pdfs[i]);
        row = { name: pdfs[i].name, state: "ok", title: r.title };
      } catch (e) {
        const msg = e instanceof Error ? e.message : "error";
        row =
          msg === "no_match"
            ? { name: pdfs[i].name, state: "nomatch" }
            : msg === "ambiguous"
              ? { name: pdfs[i].name, state: "ambiguous" }
              : { name: pdfs[i].name, state: "error", error: errText(e) };
      }
      setRows((prev) => prev.map((r, j) => (j === i ? row : r)));
    }
    onDone?.();
  }

  // R1: at least one PDF over the container → active. In a real browser `files` is
  // unreadable on dragover (only `types`): a bare "Files" type activates then, the
  // non-PDF filtering happens at drop time.
  function pdfDrag(e: React.DragEvent) {
    const files = Array.from(e.dataTransfer?.files ?? []);
    return files.length > 0
      ? files.some(isPdf)
      : e.dataTransfer?.types?.includes("Files") === true;
  }
  function onDragEnter(e: React.DragEvent) {
    if (!pdfDrag(e)) return;
    e.preventDefault();
    depth.current += 1;
    setActive(true);
  }
  function onDragOver(e: React.DragEvent) {
    if (pdfDrag(e)) e.preventDefault(); // allow the drop
  }
  function end() {
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setActive(false);
  }
  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    end();
    void handleFiles(Array.from(e.dataTransfer?.files ?? []));
  }

  const fileInput = (
    <input
      ref={inputRef}
      type="file"
      accept=".pdf"
      multiple
      disabled={rows.some((r) => r.state === "pending")}
      className="hidden"
      onChange={(e) => {
        void handleFiles(Array.from(e.target.files ?? []));
        e.target.value = "";
      }}
    />
  );

  // R3: the result panel (mockup 02) — announced, each status has its text.
  const panel = rows.length > 0 && (
    <section role="status" aria-live="polite" className="overflow-hidden rounded-xl border border-border bg-surface">
      <header className="flex items-center gap-2 border-b border-border px-3.5 py-2.5 text-[13px] font-semibold text-foreground">
        <span>{t("bulkPanel", { n: rows.length })}</span>
        <button
          type="button"
          onClick={() => setRows([])}
          aria-label={tc("close")}
          className="ml-auto cursor-pointer border-0 bg-transparent p-1 text-base leading-none text-muted hover:text-foreground"
        >
          ×
        </button>
      </header>
      <ul>
        {rows.map((r, i) => (
          <li
            key={i}
            className="flex items-center gap-2.5 border-t border-border/50 px-3.5 py-2 text-[13px] first:border-t-0"
          >
            <span className="min-w-[190px] font-mono text-xs text-foreground">{r.name}</span>
            <span className={ROW_TONE[r.state]}>
              {r.state === "pending" && `⋯ ${t("bulkBusy")}`}
              {r.state === "ok" && (
                <>
                  ✓{" "}
                  {/* Rich interpolation: the article title stays foreground, the rest is success. */}
                  {t.rich("bulkMatched", {
                    title: () => <span className="text-foreground">{r.title ?? t("untitled")}</span>,
                  })}
                </>
              )}
              {r.state === "nomatch" && (
                <>
                  ✗ {t("bulkNoMatchLabel")} — {t("bulkNoMatch")}
                </>
              )}
              {r.state === "ambiguous" && (
                <>
                  ⚠ {t("bulkAmbiguousLabel")} — {t("bulkAmbiguous")}
                </>
              )}
              {r.state === "error" && <>✗ {r.error}</>}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );

  // The central drop block content (mockup 01): icon + title + hint + pick button.
  const dropContent = (
    <>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-11 w-11 text-primary" aria-hidden>
        <path d="M7 3h7l4 4v14H7z" />
        <path d="M14 3v4h4" />
        <path d="M12 10v6m0 0-2.5-2.5M12 16l2.5-2.5" strokeDasharray="2.5 2" />
      </svg>
      <p className="text-lg font-bold text-foreground">{t("bulkDropTitle")}</p>
      <p className="max-w-[420px] text-center text-[13px] text-muted">{t("bulkDropHint")}</p>
      <Button size="sm" onClick={() => inputRef.current?.click()}>
        {t("bulkAdd")}
      </Button>
    </>
  );

  if (variant === "inline") {
    return (
      <div className="flex flex-col gap-3">
        {panel}
        <div
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={end}
          onDrop={onDrop}
          onDragEnd={end}
          className={`flex flex-col items-center gap-2.5 rounded-xl border-2 border-dashed px-6 py-8 ${
            active ? "border-primary bg-primary/5" : "border-border bg-surface"
          }`}
        >
          {dropContent}
          {fileInput}
        </div>
      </div>
    );
  }

  return (
    <>
      {panel}
      <div
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={end}
        onDrop={onDrop}
        onDragEnd={end}
        className={`relative rounded-xl ${active ? "border-2 border-dashed border-primary" : ""}`}
      >
        {children}
        {active && (
          <div className="absolute inset-0 z-10 flex items-center justify-center" aria-busy="true">
            <span aria-hidden className="pointer-events-none absolute inset-0 rounded-xl bg-primary/5" />
            <div role="status" className="flex flex-col items-center gap-2.5 rounded-2xl bg-surface px-9 py-6 shadow-lg">
              {dropContent}
            </div>
          </div>
        )}
        {fileInput}
      </div>
    </>
  );
}
