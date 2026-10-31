"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { matchArticlePdf } from "@/lib/api";

type Row = { name: string; state: "wait" | "ok" | "no_match" | "ambiguous" | "error"; title?: string | null; error?: string };

/** Upload of several PDFs at once: each is linked to the article whose title / DOI it carries. */
export function BulkPdfUpload({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const t = useTranslations("articles");
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);

  async function run(files: FileList | null) {
    if (!files || files.length === 0) return;
    const list = Array.from(files);
    setBusy(true);
    setRows(list.map((f) => ({ name: f.name, state: "wait" })));
    for (let i = 0; i < list.length; i++) {
      let next: Row;
      try {
        const r = await matchArticlePdf(projectId, list[i]);
        next = { name: list[i].name, state: "ok", title: r.title };
      } catch (e) {
        const msg = e instanceof Error ? e.message : "error";
        next =
          msg === "no_match" || msg === "ambiguous"
            ? { name: list[i].name, state: msg }
            : { name: list[i].name, state: "error", error: msg };
      }
      setRows((prev) => prev.map((r, j) => (j === i ? next : r)));
    }
    setBusy(false);
    onDone();
  }

  return (
    <div className="flex flex-col gap-2">
      <label className="inline-block cursor-pointer text-sm font-medium text-primary hover:underline">
        {busy ? t("bulkBusy") : t("bulkAdd")}
        <input
          type="file"
          accept="application/pdf"
          multiple
          className="hidden"
          disabled={busy}
          onChange={(e) => {
            void run(e.target.files);
            e.target.value = "";
          }}
        />
      </label>
      <p className="text-xs text-muted">{t("bulkHint")}</p>
      {rows.length > 0 && (
        <ul className="flex flex-col gap-1 text-xs">
          {rows.map((r, i) => (
            <li key={i} className={r.state === "ok" ? "text-success" : r.state === "wait" ? "text-muted" : "text-danger"}>
              {r.state === "wait" && "… "}
              {r.state === "ok" && "✓ "}
              {(r.state === "no_match" || r.state === "ambiguous" || r.state === "error") && "✗ "}
              <span className="font-mono">{r.name}</span>
              {r.state === "ok" && ` → ${r.title ?? ""}`}
              {r.state === "no_match" && ` — ${t("bulkNoMatch")}`}
              {r.state === "ambiguous" && ` — ${t("bulkAmbiguous")}`}
              {r.state === "error" && ` — ${r.error}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
