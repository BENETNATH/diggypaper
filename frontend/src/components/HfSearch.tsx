"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { searchHf, hfFiles, installHf, pullOllama, HfModel, HfFile } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";

type Kind = "chat" | "encoder";

const fmtSize = (n?: number | null) =>
  n ? (n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`) : "";

/** Recherche Hugging Face (GGUF via Ollama / ONNX) → choix de la variante → installation. */
export function HfSearch({
  onStarted,
  onError,
}: {
  onStarted: () => void;
  onError: (m: string) => void;
}) {
  const t = useTranslations("models");
  const [q, setQ] = useState("");
  const kind: Kind = "chat"; // génératifs (GGUF via Ollama) ; les modèles de décision ont leur formulaire
  const [results, setResults] = useState<HfModel[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [repo, setRepo] = useState<string | null>(null);
  const [files, setFiles] = useState<HfFile[] | null>(null);
  const [noTokenizer, setNoTokenizer] = useState(false);
  const [choice, setChoice] = useState("");
  const [started, setStarted] = useState(false);

  async function search(e: React.FormEvent) {
    e.preventDefault();
    setSearching(true);
    setStarted(false);
    setRepo(null);
    try {
      setResults(await searchHf(q.trim(), kind));
    } catch (err) {
      onError(err instanceof Error ? err.message : "error");
    } finally {
      setSearching(false);
    }
  }

  async function open(id: string) {
    setRepo(id);
    setFiles(null);
    setChoice("");
    if (results?.find((r) => r.id === id)?.decision) return;
    try {
      const r = await hfFiles(id, kind);
      setFiles(r.items);
      setNoTokenizer(kind === "encoder" && r.tokenizer === false);
      // Présélection : la variante recommandée pour le CPU, sinon la première (déjà triée).
      setChoice(kind === "chat" ? (r.items.find((f) => f.recommended) ?? r.items[0])?.quant ?? "" : r.items[0]?.file ?? "");
    } catch (err) {
      onError(err instanceof Error ? err.message : "error");
    }
  }

  async function install() {
    if (!repo) return;
    try {
      await installHf(
        kind === "chat"
          ? { repo, kind, ...(choice ? { quant: choice } : {}) }
          : { repo, kind, file: choice }
      );
      done();
    } catch (err) {
      onError(err instanceof Error ? err.message : "error");
    }
  }

  async function installOllama(name: string) {
    try {
      await pullOllama(name);
      done();
    } catch (err) {
      onError(err instanceof Error ? err.message : "error");
    }
  }

  function done() {
    setResults(null);
    setRepo(null);
    setQ("");
    setStarted(true);
    onStarted();
  }

  // Nom Ollama direct (« qwen3:4b ») : pas de recherche possible côté Ollama.
  const ollamaName = kind === "chat" && /^[\w.-]+(:[\w.-]+)?$/.test(q.trim()) ? q.trim() : null;

  return (
    <section>
      <h3 className="mb-2 text-sm font-semibold text-foreground">{t("searchTitle")}</h3>
      <p className="mb-3 text-sm text-muted">{t("searchHint")}</p>
      <form onSubmit={search} className="flex flex-wrap gap-2">
        <div className="min-w-[12rem] flex-1">
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="qwen, gemma, llama…" minLength={2} required />
        </div>
        <Button type="submit" loading={searching} disabled={q.trim().length < 2}>
          {t("search")}
        </Button>
      </form>

      {started && <p className="mt-3 text-sm text-success">✓ {t("started")}</p>}
      {!results && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
          {t("tryLabel")}
          {["qwen3", "gemma3", "llama3", "phi4"].map((s0) => (
            <button
              key={s0}
              type="button"
              className="rounded-full border border-border px-2.5 py-1 hover:bg-surface-muted"
              onClick={() => {
                setQ(s0);
                setStarted(false);
              }}
            >
              {s0}
            </button>
          ))}
        </div>
      )}
      {ollamaName && results && (
        <button
          type="button"
          onClick={() => void installOllama(ollamaName)}
          className="mt-3 w-full rounded-lg border border-dashed border-border p-2 text-left text-sm hover:bg-surface-muted"
        >
          {t("fromOllama", { name: ollamaName })}
        </button>
      )}
      {results && results.length === 0 && <p className="mt-3 text-sm text-muted">{t("noResults")}</p>}
      {results && results.length > 0 && (
        <ul className="mt-3 divide-y divide-border">
          {results.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                onClick={() => void open(m.id)}
                className={`flex w-full items-center justify-between gap-3 py-2 text-left hover:bg-surface-muted ${repo === m.id ? "bg-surface-muted" : ""}`}
              >
                <span className="min-w-0 truncate font-mono text-sm">
                  {m.id}
                  {m.decision && (
                    <span className="ml-2 rounded-full bg-primary/10 px-2 py-0.5 font-sans text-xs text-primary">
                      {t("decisionBadge")}
                    </span>
                  )}
                </span>
                <span className="shrink-0 text-xs text-muted">↓ {m.downloads.toLocaleString()}</span>
              </button>
              {repo === m.id && m.decision && (
                <p className="mb-3 rounded-lg bg-warning/10 p-3 text-sm text-foreground">{t("decisionNotChat")}</p>
              )}
              {repo === m.id && !m.decision && (
                <div className="mb-3 flex flex-col gap-2 rounded-lg bg-surface-muted p-3">
                  {!files ? (
                    <p className="text-sm text-muted">…</p>
                  ) : files.length === 0 ? (
                    <p className="text-sm text-warning">{t("noVariants")}</p>
                  ) : (
                    <>
                      <label className="text-sm font-medium">{kind === "chat" ? t("pickQuant") : t("pickFile")}</label>
                      <Select value={choice} onChange={(e) => setChoice(e.target.value)}>
                        {files.map((f) => (
                          <option key={f.file} value={kind === "chat" ? f.quant ?? "" : f.file}>
                            {kind === "chat" ? `${f.quant ?? f.file}` : f.file} {fmtSize(f.size)}
                            {f.recommended ? ` ★ ${t("recommended")}` : ""}
                          </option>
                        ))}
                      </Select>
                      {kind === "chat" && !files.some((f) => f.recommended) && (
                        <p className="text-xs text-warning">{t("noRecommended")}</p>
                      )}
                      {noTokenizer && <p className="text-xs text-warning">{t("noTokenizer")}</p>}
                      <div>
                        <Button size="sm" onClick={() => void install()} disabled={noTokenizer}>
                          {t("install")}
                        </Button>
                      </div>
                    </>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

    </section>
  );
}
