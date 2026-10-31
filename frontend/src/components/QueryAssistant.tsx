"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { assistQuery, listModels, ModelInfo, QueryAssist } from "@/lib/api";
import { sourceName } from "@/lib/source";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Field";
import { ErrorBanner } from "@/components/ui/Feedback";

type Turn = { role: "user" | "assistant"; content: string; result?: QueryAssist };

/**
 * Query assistant: we describe the search in free text, a model proposes the query in
 * the syntax of the chosen engine (`source`). Each proposal is verified by the source (volume;
 * on PubMed also: unknown terms and, with expected articles (DOI / PMID), how many it
 * finds). The model is used only for that (bounded server-side).
 */
export function QueryAssistant({
  projectId,
  source,
  onUse,
}: {
  projectId: string;
  source: string;
  onUse: (query: string) => void;
}) {
  const t = useTranslations("import");
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [seeds, setSeeds] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listModels(projectId)
      .then((m) => {
        setModels(m);
        setModel((cur) => cur || m[0]?.name || "");
      })
      .catch(() => setModels([]));
  }, [projectId]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || !model || busy) return;
    const next: Turn[] = [...turns, { role: "user", content: text }];
    setTurns(next);
    setInput("");
    setBusy(true);
    setError(null);
    try {
      // The model receives the conversation text, with the query it proposed on each turn.
      const messages = next.map((m) => ({
        role: m.role,
        content: m.result?.query ? `${m.content}\nQuery: ${m.result.query}`.slice(0, 1000) : m.content,
      }));
      const r = await assistQuery(projectId, { model, source, messages: messages.slice(-8), seeds });
      setTurns([...next, { role: "assistant", content: r.reply, result: r }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setBusy(false);
    }
  }

  if (models.length === 0) return <p className="text-sm text-muted">{t("qaNoModel")}</p>;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted">{t("qaIntro")}</p>
      <label className="flex max-w-xs flex-col gap-1 text-sm">
        <span className="font-medium text-foreground">{t("qaModel")}</span>
        <Select value={model} onChange={(e) => setModel(e.target.value)}>
          {models.map((m) => (
            <option key={m.id} value={m.name}>
              {m.name}
            </option>
          ))}
        </Select>
      </label>
      {source === "pubmed" && (
      <details className="text-sm">
        <summary className="cursor-pointer font-medium text-foreground">{t("qaSeeds")}</summary>
        <p className="mb-1 mt-1 text-xs text-muted">{t("qaSeedsHint")}</p>
        <textarea
          value={seeds}
          onChange={(e) => setSeeds(e.target.value)}
          rows={3}
          maxLength={20000}
          placeholder={"10.1000/xyz123\nPMID: 12345678"}
          className="w-full rounded border border-border bg-surface p-2 font-mono text-xs"
        />
      </details>
      )}
      {turns.length > 0 && (
        <ul className="flex flex-col gap-3">
          {turns.map((m, i) => (
            <li
              key={i}
              className={`rounded-lg p-3 text-sm ${m.role === "user" ? "ml-8 bg-primary/5" : "mr-8 bg-surface-muted/60"}`}
            >
              {m.result?.refused ? (
                <p className="text-muted">{t("qaRefused")}</p>
              ) : (
                m.content && <p className="whitespace-pre-wrap text-foreground">{m.content}</p>
              )}
              {m.result?.query && <Proposal result={m.result} source={source} onUse={onUse} />}
            </li>
          ))}
        </ul>
      )}
      {error && <ErrorBanner message={error} />}
      <form onSubmit={send} className="flex flex-col gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          rows={2}
          maxLength={1000}
          placeholder={t("qaPlaceholder")}
          aria-label={t("qaPlaceholder")}
          className="w-full rounded-lg border border-border bg-surface p-2 text-sm"
        />
        <div className="flex items-center gap-3">
          <Button type="submit" loading={busy} disabled={!input.trim() || !model}>
            {t("qaSend")}
          </Button>
          {busy && <span className="text-xs text-muted">{t("qaThinking")}</span>}
        </div>
      </form>
      <p className="text-xs text-muted">{t("qaHint")}</p>
    </div>
  );
}

function Proposal({
  result,
  source,
  onUse,
}: {
  result: QueryAssist;
  source: string;
  onUse: (q: string) => void;
}) {
  const t = useTranslations("import");
  const c = result.check;
  const src = sourceName(source) ?? source;
  return (
    <div className="mt-2 flex flex-col gap-2">
      <pre className="whitespace-pre-wrap break-words rounded bg-surface p-2 font-mono text-xs text-foreground">
        {result.query}
      </pre>
      {c?.error ? (
        <p className="text-xs text-warning">
          {(() => {
            const down = sourceName(c.error!.replace(/_unavailable$/, ""));
            return down ? t("srcDown", { src: down }) : t("qaNcbiDown");
          })()}
        </p>
      ) : c ? (
        <div className="flex flex-col gap-1 text-xs">
          <p className="text-foreground">
            {t("qaResults", { n: c.count ?? 0, src })}
            {c.recall && (
              <span className={c.recall.found < c.recall.total ? "text-warning" : "text-success"}>
                {" · "}
                {t("qaRecall", { found: c.recall.found, total: c.recall.total })}
              </span>
            )}
          </p>
          {c.recall && c.recall.missing.length > 0 && (
            <p className="text-warning">{t("qaMissing", { list: c.recall.missing.join(", ") })}</p>
          )}
          {c.not_found && c.not_found.length > 0 && (
            <p className="text-danger">{t("qaNotFound", { list: c.not_found.join(", "), src })}</p>
          )}
          {c.too_long && <p className="text-danger">{t("qaTooLong")}</p>}
          {c.translation && (
            <details>
              <summary className="cursor-pointer text-muted">{t("qaTranslation", { src })}</summary>
              <p className="mt-1 break-words font-mono text-muted">{c.translation}</p>
            </details>
          )}
        </div>
      ) : null}
      <div>
        <Button type="button" size="sm" variant="secondary" disabled={!!c?.too_long} onClick={() => onUse(result.query!)}>
          {t("qaUse")}
        </Button>
      </div>
    </div>
  );
}
