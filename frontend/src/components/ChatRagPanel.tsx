"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  streamChat,
  semanticSearch,
  triggerVectorize,
  ApiError,
  ChatCitation,
  ModelInfo,
} from "@/lib/api";
import { useSearchId } from "@/lib/useSearch";
import { withSearch } from "@/lib/search";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/ui/Feedback";

type Props = {
  projectId: string;
  /** Models the member can use (from the project, already filtered). */
  models: ModelInfo[];
  /** Lead or admin of the project (shows the « Indexer le corpus » button). */
  isLead: boolean;
};

type Exchange =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; citations: ChatCitation[] };

type Current = {
  question: string;
  answer: string;
  citations: ChatCitation[];
  streaming: boolean;
};

const HISTORY_MAX = 12; // user/assistant messages sent with the request (≤ 6 pairs)

/** Renders `[n]` citation markers of a streamed answer as highlighted marks. */
function renderCited(text: string) {
  return text.split(/(\[\d+\])/g).map((part, i) =>
    /^\[\d+\]$/.test(part) ? (
      <mark key={i} className="rounded bg-amber-200/70 px-1 text-foreground">
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    )
  );
}

/** The « Sources citées » block under an answer (streaming or committed). */
function CitationsList({
  citations,
  projectId,
  searchId,
}: {
  citations: ChatCitation[];
  projectId: string;
  searchId: string | undefined;
}) {
  const t = useTranslations("chatRag");
  if (citations.length === 0) return null;
  return (
    <div data-testid="chat-citations" className="mt-2">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{t("citations")}</p>
      <ul className="mt-1 flex flex-col gap-1">
        {citations.map((c) => (
          <li key={c.n} className="rounded-lg border border-border bg-surface p-2 text-sm">
            <Link
              href={withSearch(`/projects/${projectId}/annotate?article=${c.article_id}`, searchId)}
              className="font-medium text-primary hover:underline"
            >
              [{c.n}] {c.title || t("untitled")}
            </Link>
            <p className="mt-1 line-clamp-2 text-muted">{c.excerpt}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Chat on the corpus (spec 2026-11-07-chat-rag §5): a question over the
 * vectorized corpus, streamed (SSE), with `[n]` citations resolved to the
 * cited sections. No persistence (RGPD): the history lives in this
 * component's state only.
 */
export function ChatRagPanel({ projectId, models, isLead }: Props) {
  const t = useTranslations("chatRag");
  const searchId = useSearchId(projectId);
  const defaultModel = models.find((m) => m.backend_type === "OLLAMA") ?? models[0];
  const [model, setModel] = useState("");
  const effectiveModel = model || defaultModel?.name || "";
  const [indexed, setIndexed] = useState<boolean | undefined>(undefined);
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Exchange[]>([]);
  const [current, setCurrent] = useState<Current | null>(null);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [indexing, setIndexing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Probe the corpus state (no embedding: a punctuation-only query returns
  // early with the `indexed` flag).
  useEffect(() => {
    let cancelled = false;
    semanticSearch(projectId, ".", 1)
      .then((r) => {
        if (!cancelled) setIndexed(r.indexed);
      })
      .catch(() => {
        if (!cancelled) setIndexed(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Elapsed-time ticker while the answer streams (latence assumée).
  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(id);
  }, [busy]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  function commitExchange(q: string, done: { full_text: string }, citations: ChatCitation[]) {
    setHistory((h) =>
      [
        ...h,
        { role: "user" as const, content: q },
        { role: "assistant" as const, content: done.full_text, citations },
      ].slice(-HISTORY_MAX)
    );
  }

  async function send() {
    const q = question.trim();
    if (!q || busy || !effectiveModel) return;
    setBusy(true);
    setElapsed(0);
    setError(null);
    let citations: ChatCitation[] = [];
    setCurrent({ question: q, answer: "", citations: [], streaming: true });
    const abort = new AbortController();
    abortRef.current = abort;
    // The API history is role/content only (citations are display-only).
    const sent = history.slice(-HISTORY_MAX).map((m) => ({ role: m.role, content: m.content }));
    try {
      await streamChat(
        projectId,
        { question: q, model: effectiveModel, history: sent },
        (chunk) => setCurrent((c) => (c ? { ...c, answer: c.answer + chunk } : c)),
        (c) => {
          citations = c;
          setCurrent((cur) => (cur ? { ...cur, citations: c } : cur));
        },
        (done) => {
          // Commit the finished exchange to the history and clear the
          // streaming bubble — the answer renders exactly once.
          commitExchange(q, done, citations);
          setCurrent(null);
        },
        (detail) => {
          setCurrent((c) => (c ? { ...c, streaming: false } : c));
          if (detail.startsWith("model_busy")) setError(t("modelBusy"));
          else if (detail.startsWith("timeout")) setError(t("timeout"));
          else setError(t("error"));
        },
        abort.signal
      );
      setCurrent((c) => (c ? { ...c, streaming: false } : c));
    } catch (e) {
      setCurrent((c) => (c ? { ...c, streaming: false } : c));
      if (e instanceof ApiError) {
        if (e.detail.startsWith("model_busy")) setError(t("modelBusy"));
        else if (e.detail.startsWith("corpus_empty")) setError(t("emptyCorpus"));
        else setError(e.detail);
      } else {
        setError(t("error"));
      }
    } finally {
      setBusy(false);
    }
  }

  async function indexCorpus() {
    if (indexing) return;
    setIndexing(true);
    setError(null);
    try {
      await triggerVectorize(projectId);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setIndexing(false);
    }
  }

  function clearConversation() {
    abortRef.current?.abort();
    setHistory([]);
    setCurrent(null);
    setError(null);
    setBusy(false);
  }

  const canSend = Boolean(effectiveModel) && question.trim() !== "" && !busy && indexed !== false;

  return (
    <div data-testid="chat-rag-panel" className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs font-medium uppercase tracking-wide text-muted" htmlFor="chat-rag-model">
          {t("model")}
        </label>
        <select
          id="chat-rag-model"
          value={effectiveModel}
          onChange={(e) => setModel(e.target.value)}
          disabled={busy || models.length === 0}
          className="rounded-lg border border-border bg-surface px-2 py-1 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
        >
          {models.length === 0 && <option value="">{t("noModel")}</option>}
          {models.map((m) => (
            <option key={m.id} value={m.name}>
              {m.name}
            </option>
          ))}
        </select>
        {(history.length > 0 || current !== null) && (
          <Button type="button" variant="ghost" size="sm" onClick={() => clearConversation()}>
            {t("clear")}
          </Button>
        )}
      </div>

      {indexed === false && !busy && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <span data-testid="chat-not-indexed">{t("notIndexed")}</span>
          {isLead && (
            <Button
              variant="secondary"
              size="sm"
              data-testid="chat-index-button"
              onClick={() => void indexCorpus()}
              disabled={indexing}
            >
              {t("indexButton")}
            </Button>
          )}
          {indexing && <span data-testid="chat-index-started">{t("indexStarted")}</span>}
        </div>
      )}

      {error && <ErrorBanner message={error} />}

      {history.length === 0 && current === null && (
        <p data-testid="chat-empty" className="text-sm text-muted">
          {t("emptyHistory")}
        </p>
      )}

      <ul className="flex flex-col gap-3">
        {history.map((m, i) => (
          <li key={i} className={m.role === "user" ? "text-right" : ""}>
            <div
              data-testid={m.role === "user" ? "chat-user-turn" : "chat-assistant-turn"}
              className={`ml-auto max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                m.role === "user"
                  ? "bg-primary/10 text-foreground"
                  : "border border-border bg-surface-muted text-foreground"
              }`}
            >
              {m.role === "user" ? m.content : renderCited(m.content)}
            </div>
            {m.role === "assistant" && (
              <CitationsList citations={m.citations} projectId={projectId} searchId={searchId} />
            )}
          </li>
        ))}
        {current && (
          <li>
            <div className="ml-auto max-w-[85%] whitespace-pre-wrap rounded-lg bg-primary/10 px-3 py-2 text-sm text-foreground">
              {current.question}
            </div>
            <div
              data-testid="chat-current-answer"
              className="mt-2 max-w-[95%] whitespace-pre-wrap rounded-lg border border-border bg-surface-muted px-3 py-2 text-sm text-foreground"
            >
              {renderCited(current.answer)}
              {current.streaming && (
                <span data-testid="chat-streaming" className="ml-1 inline-block animate-pulse">
                  ▌
                </span>
              )}
            </div>
            {current.streaming && (
              <p data-testid="chat-thinking" className="mt-1 text-xs text-muted">
                {t("thinking")} {elapsed}s
              </p>
            )}
            {!current.streaming && (
              <CitationsList citations={current.citations} projectId={projectId} searchId={searchId} />
            )}
          </li>
        )}
      </ul>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          type="search"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={t("placeholder")}
          aria-label={t("placeholder")}
          data-testid="chat-input"
          disabled={busy || indexed === false}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
        />
        <Button type="submit" size="sm" disabled={!canSend}>
          {busy ? t("thinking") : t("send")}
        </Button>
      </form>
    </div>
  );
}
