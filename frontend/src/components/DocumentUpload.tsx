"use client";

import { useCallback, useRef, useState } from "react";
import { useTranslations } from "next-intl";

type FileState = "pending" | "uploading" | "done" | "duplicate" | "error";

interface FileItem {
  file: File;
  state: FileState;
  progress: number;
  error?: string;
}

const MAX_SIZE = 25 * 1024 * 1024; // 25 MB

export function DocumentUpload({ projectId }: { projectId: string }) {
  const t = useTranslations("project");
  const [files, setFiles] = useState<FileItem[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const queueRef = useRef<FileItem[]>([]);
  const busyRef = useRef(false);

  const processQueue = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    while (queueRef.current.length > 0) {
      const item = queueRef.current[0];
      if (item.state !== "pending") { queueRef.current.shift(); continue; }

      setFiles((prev) => prev.map((f) => f.file === item.file ? { ...f, state: "uploading", progress: 0 } : f));

      try {
        const xhr = new XMLHttpRequest();
        const url = `/api/v1/projects/${projectId}/documents?filename=${encodeURIComponent(item.file.name)}`;
        xhr.open("POST", url);
        xhr.setRequestHeader("Content-Type", item.file.type || "application/octet-stream");
        const token = document.cookie.split("; ").find(c => c.startsWith("token="))?.split("=")[1];
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

        await new Promise<void>((resolve, reject) => {
          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
              const pct = Math.round((e.loaded / e.total) * 100);
              setFiles((prev) => prev.map((f) => f.file === item.file ? { ...f, progress: pct } : f));
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
        const newState: FileState = data.duplicate ? "duplicate" : "done";
        setFiles((prev) => prev.map((f) => f.file === item.file ? { ...f, state: newState, progress: 100 } : f));
      } catch (err) {
        queueRef.current.shift();
        setFiles((prev) => prev.map((f) => f.file === item.file ? { ...f, state: "error", error: String(err) } : f));
      }
    }
    busyRef.current = false;
  }, [projectId]);

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
    setFiles((prev) => prev.map((f) => f.file === file.file ? { ...f, state: "pending", progress: 0, error: undefined } : f));
    queueRef.current.push(file);
    void processQueue();
  }, [processQueue]);

  const totalDone = files.filter((f) => f.state === "done" || f.state === "duplicate").length;
  const totalFiles = files.length;

  return (
    <div className="flex flex-col gap-2">
      {/* Drop zone */}
      <div
        className={`rounded-lg border-2 border-dashed p-4 text-center text-sm transition-colors ${
          dragOver ? "border-primary bg-primary/10" : "border-border"
        }`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter") inputRef.current?.click(); }}
      >
        {t("uploadHint")}
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".txt,.pdf"
          hidden
          onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }}
        />
      </div>

      {/* Progress bar */}
      {totalFiles > 0 && (
        <div className="flex items-center gap-2 text-xs text-muted">
          <span>{totalDone}/{totalFiles}</span>
          <div className="h-1 flex-1 rounded-full bg-border">
            <div
              className="h-1 rounded-full bg-primary transition-all"
              style={{ width: `${totalFiles > 0 ? (totalDone / totalFiles) * 100 : 0}%` }}
            />
          </div>
        </div>
      )}

      {/* File list */}
      {files.length > 0 && (
        <ul className="flex flex-col gap-1">
          {files.map((f) => (
            <li key={f.file.name} className="flex items-center justify-between gap-2 rounded px-2 py-1 text-xs">
              <span className="truncate">{f.file.name}</span>
              <span className="flex items-center gap-1 shrink-0">
                {f.state === "pending" && <span className="text-muted">{t("uploadWaiting")}</span>}
                {f.state === "uploading" && <span className="text-primary">{t("uploadProgress", { n: f.progress })}</span>}
                {f.state === "done" && <span className="text-green-600">{t("uploadCreated")}</span>}
                {f.state === "duplicate" && <span className="text-muted">{t("uploadDuplicate")}</span>}
                {f.state === "error" && (
                  <>
                    <span className="text-red-600">{t("uploadError")}</span>
                    <button type="button" onClick={() => retryFile(f)} className="text-primary hover:underline">
                      {t("uploadRetry")}
                    </button>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
