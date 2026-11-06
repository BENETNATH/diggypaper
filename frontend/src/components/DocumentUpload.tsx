"use client";

import { useCallback, useRef, useState } from "react";
import { useTranslations } from "next-intl";

type FileState =
  | "pending"
  | "uploading"
  | "done"
  | "duplicate"
  | "error"
  | "ocrRunning"
  | "ocrDone"
  | "ocrError";

interface FileItem {
  file: File;
  state: FileState;
  progress: number;
  error?: string;
  /** Article created by the upload (OCR target). */
  articleId?: string;
  /** PDF without text layer → OCR available. */
  needsOcr?: boolean;
  /** PDF page count (OCR time estimate, spec 2026-11-06-ux-refonte-v4 R2). */
  pageCount?: number;
}

const MAX_SIZE = 25 * 1024 * 1024; // 25 MB

const OCR_STATES: FileState[] = ["done", "ocrRunning", "ocrDone", "ocrError"];

function tokenFromCookie(): string | undefined {
  return document.cookie.split("; ").find((c) => c.startsWith("token="))?.split("=")[1];
}

export function DocumentUpload({
  projectId,
  onSettled,
}: {
  projectId: string;
  /** Called once the upload queue finishes (articles created) — the page refreshes. */
  onSettled?: () => void;
}) {
  const t = useTranslations("project");
  const [files, setFiles] = useState<FileItem[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [ocr, setOcr] = useState<{ running: boolean; done: number; total: number }>({ running: false, done: 0, total: 0 });
  const inputRef = useRef<HTMLInputElement>(null);
  const queueRef = useRef<FileItem[]>([]);
  const busyRef = useRef(false);
  const ocrBusyRef = useRef(false);

  const patchFile = (item: FileItem, patch: Partial<FileItem>) =>
    setFiles((prev) => prev.map((f) => (f.file === item.file ? { ...f, ...patch } : f)));

  const processQueue = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    let settled = 0;
    while (queueRef.current.length > 0) {
      const item = queueRef.current[0];
      if (item.state !== "pending") {
        queueRef.current.shift();
        continue;
      }

      setFiles((prev) => prev.map((f) => (f.file === item.file ? { ...f, state: "uploading", progress: 0 } : f)));

      try {
        const xhr = new XMLHttpRequest();
        const url = `/api/v1/projects/${projectId}/documents?filename=${encodeURIComponent(item.file.name)}`;
        xhr.open("POST", url);
        xhr.setRequestHeader("Content-Type", item.file.type || "application/octet-stream");
        const token = tokenFromCookie();
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

        await new Promise<void>((resolve, reject) => {
          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
              const pct = Math.round((e.loaded / e.total) * 100);
              setFiles((prev) => prev.map((f) => (f.file === item.file ? { ...f, progress: pct } : f)));
            }
          };
          xhr.onload = () => {
            if (xhr.status === 200 || xhr.status === 201) resolve();
            else reject(new Error(`HTTP ${xhr.status}`));
          };
          xhr.onerror = () => reject(new Error("network"));
          xhr.send(item.file);
        });

        const data = JSON.parse(xhr.responseText);
        queueRef.current.shift();
        patchFile(item, {
          state: data.duplicate ? "duplicate" : "done",
          progress: 100,
          articleId: data.id,
          needsOcr: data.needs_ocr === true,
          pageCount: typeof data.page_count === "number" ? data.page_count : undefined,
        });
        settled += 1;
      } catch (err) {
        queueRef.current.shift();
        patchFile(item, { state: "error", error: String(err) });
      }
    }
    busyRef.current = false;
    if (settled > 0) onSettled?.();
  }, [projectId, onSettled]);

  const addFiles = (newFiles: FileList | File[]) => {
    const valid: FileItem[] = [];
    for (const f of Array.from(newFiles)) {
      const ext = f.name.split(".").pop()?.toLowerCase();
      if (ext !== "txt" && ext !== "pdf") continue;
      if (f.size > MAX_SIZE) continue;
      valid.push({ file: f, state: "pending", progress: 0 });
    }
    if (valid.length === 0) return;
    queueRef.current.push(...valid);
    setFiles((prev) => [...prev, ...valid]);
    void processQueue();
  };

  const retryFile = useCallback((file: FileItem) => {
    setFiles((prev) => prev.map((f) => (f.file === file.file ? { ...f, state: "pending", progress: 0, error: undefined } : f)));
    queueRef.current.push(file);
    void processQueue();
  }, [processQueue]);

  // ------------------------------------------------------------------
  // OCR — the existing per-article endpoint (rate-limit 3/min), called
  // sequentially; 429 is retried after the window (spec v4 R2).
  // ------------------------------------------------------------------
  const ocrXhr = useCallback(
    (articleId: string): Promise<number> => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/v1/projects/${projectId}/articles/${articleId}/ocr`);
      const token = tokenFromCookie();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      return new Promise((resolve) => {
        xhr.onload = () => resolve(xhr.status);
        xhr.onerror = () => resolve(0);
        xhr.send();
      });
    },
    [projectId]
  );

  const runOcrOne = useCallback(
    async (item: FileItem) => {
      if (!item.articleId) return;
      patchFile(item, { state: "ocrRunning" });
      let status = await ocrXhr(item.articleId);
      let attempt = 0;
      while (status === 429 && attempt < 5) {
        attempt += 1;
        await new Promise((r) => setTimeout(r, 19_000)); // rate-limit window (3/min)
        status = await ocrXhr(item.articleId);
      }
      if (status === 200) {
        patchFile(item, { state: "ocrDone" });
      } else {
        patchFile(item, { state: "ocrError", error: status === 422 ? "ocr_empty" : `HTTP ${status}` });
      }
    },
    [ocrXhr]
  );

  const scanned = files.filter((f) => f.needsOcr && OCR_STATES.includes(f.state));
  const launchOcr = useCallback(
    (targets: FileItem[]) => {
      if (ocrBusyRef.current || targets.length === 0) return;
      ocrBusyRef.current = true;
      setOcr({ running: true, done: 0, total: targets.length });
      void (async () => {
        for (let i = 0; i < targets.length; i++) {
          await runOcrOne(targets[i]);
          setOcr((s) => ({ ...s, done: i + 1 }));
        }
        setOcr((s) => ({ ...s, running: false }));
        ocrBusyRef.current = false;
      })();
    },
    [runOcrOne]
  );

  // R2 estimate: K × pages × 1–3 s/page on the VM, shown as a range.
  const totalPages = scanned.reduce((a, f) => a + (f.pageCount ?? 1), 0);
  const ocrEstimate =
    totalPages * 3 < 60
      ? t("ocrEstS", { low: totalPages, high: totalPages * 3 })
      : t("ocrEstMin", { low: Math.ceil(totalPages / 60), high: Math.ceil((totalPages * 3) / 60) });

  const done = files.filter((f) => f.state === "done");
  const withText = done.filter((f) => !f.needsOcr).length;
  const scannedDone = done.filter((f) => f.needsOcr);
  const showOcrPanel = scanned.length > 0;

  const totalAll = files.length;
  const totalUploaded = files.filter((f) => f.state === "done" || f.state === "duplicate" || f.state === "error").length;

  return (
    <div className="flex flex-col gap-2">
      {/* Drop zone */}
      <div
        className={`rounded-lg border-2 border-dashed p-4 text-center text-sm transition-colors ${
          dragOver ? "border-primary bg-primary/10" : "border-border"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          addFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter") inputRef.current?.click();
        }}
      >
        {t("uploadHint")}
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".txt,.pdf"
          hidden
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {/* Progress bar */}
      {totalAll > 0 && (
        <div className="flex items-center gap-2 text-xs text-muted">
          <span>{totalUploaded}/{totalAll}</span>
          <div className="h-1 flex-1 rounded-full bg-border">
            <div
              className="h-1 rounded-full bg-primary transition-all"
              style={{ width: `${totalAll > 0 ? (totalUploaded / totalAll) * 100 : 0}%` }}
            />
          </div>
        </div>
      )}

      {/* File list */}
      {files.length > 0 && (
        <ul className="flex flex-col gap-1">
          {files.map((f) => (
            <li key={f.file.name} className="flex items-center justify-between gap-2 rounded px-2 py-1 text-xs">
              <span className="truncate">
                {f.file.name}
                {f.needsOcr && OCR_STATES.includes(f.state) && (
                  <span className="ml-2 rounded bg-warning/20 px-1.5 py-0.5 text-muted">{t("scannedBadge")}</span>
                )}
              </span>
              <span className="flex items-center gap-1 shrink-0">
                {f.state === "pending" && <span className="text-muted">{t("uploadWaiting")}</span>}
                {f.state === "uploading" && <span className="text-primary">{t("uploadProgress", { n: f.progress })}</span>}
                {f.state === "done" && (
                  <>
                    <span className="text-green-600">{t("uploadCreated")}</span>
                    {f.needsOcr && (
                      <button
                        type="button"
                        onClick={() => void runOcrOne(f)}
                        className="rounded border border-border px-1.5 py-0.5 text-primary hover:bg-primary/10"
                      >
                        {t("runOcr")}
                      </button>
                    )}
                  </>
                )}
                {f.state === "duplicate" && <span className="text-muted">{t("uploadDuplicate")}</span>}
                {f.state === "error" && (
                  <>
                    <span className="text-red-600">{t("uploadError")}</span>
                    <button type="button" onClick={() => retryFile(f)} className="text-primary hover:underline">
                      {t("uploadRetry")}
                    </button>
                  </>
                )}
                {f.state === "ocrRunning" && <span className="text-primary">{t("ocrRunning")}</span>}
                {f.state === "ocrDone" && <span className="text-green-600">{t("ocrDone")}</span>}
                {f.state === "ocrError" && (
                  <>
                    <span className="text-red-600">{f.error === "ocr_empty" ? t("ocrEmpty") : t("ocrFailed")}</span>
                    <button
                      type="button"
                      onClick={() => void runOcrOne(f)}
                      className="text-primary hover:underline"
                    >
                      {t("uploadRetry")}
                    </button>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* R2 — OCR confirmation panel, after the upload queue settles */}
      {showOcrPanel && (
        <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-3 text-sm">
          <p className="text-muted">
            {t("ocrSummary", { n: done.length, m: withText, k: scannedDone.length })}
            {ocr.running && (
              <span className="ml-2 text-primary">
                {t("ocrProgress", { done: ocr.done, total: ocr.total })}
              </span>
            )}
          </p>
          {scannedDone.length > 0 && (
            <ul className="flex flex-col gap-0.5 text-xs text-muted">
              {scannedDone.map((f) => (
                <li key={f.file.name} className="truncate">
                  {f.file.name}
                  {typeof f.pageCount === "number" && <> · {t("ocrPages", { n: f.pageCount })}</>}
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            disabled={ocr.running || scannedDone.length === 0}
            onClick={() => launchOcr(scannedDone)}
            className="self-start rounded bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {t("ocrLaunch", { k: scannedDone.length, est: ocrEstimate })}
          </button>
        </div>
      )}
    </div>
  );
}
