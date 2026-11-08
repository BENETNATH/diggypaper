"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { semanticSearch, SemanticResult } from "@/lib/api";
import { useSearchId } from "@/lib/useSearch";
import { withSearch } from "@/lib/search";
import { useVectorizeProgress } from "@/hooks/useVectorizeProgress";
import { IndexProgress } from "@/components/IndexProgress";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/ui/Feedback";

type Props = {
  projectId: string;
  /** Lead or admin of the project (shows the « Indexer le corpus » button). */
  isLead: boolean;
};

/**
 * RAG semantic search box (spec 2026-11-07-recherche-semantique-rag §5):
 * query → hybrid dense+lexical results (article, section, excerpt, score).
 * An unvectorized corpus (``indexed: false``) shows the "not indexed yet"
 * state with a lead-only « Indexer le corpus » trigger.
 */
export function SemanticSearchBox({ projectId, isLead }: Props) {
  const t = useTranslations("semantic");
  const searchId = useSearchId(projectId);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SemanticResult[] | null>(null);
  const [searchIndexed, setSearchIndexed] = useState<boolean | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Live corpus-index state + trigger (one cheap status read, not a probe
  // semantic search); the hook polls while a run is in flight.
  const { progress, indexing, index } = useVectorizeProgress(projectId);
  const indexed = progress?.indexed ?? searchIndexed;

  async function runSearch(q: string) {
    const value = q.trim();
    if (!value || loading) return;
    setLoading(true);
    setError(null);
    try {
      const r = await semanticSearch(projectId, value);
      setResults(r.results);
      setSearchIndexed(r.indexed);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResults(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      data-testid="semantic-box"
      className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-3"
    >
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void runSearch(query);
        }}
      >
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("placeholder")}
          aria-label={t("placeholder")}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
        />
        <Button type="submit" size="sm" disabled={loading || !query.trim()}>
          {loading ? t("loading") : t("search")}
        </Button>
      </form>

      {error && <ErrorBanner message={error} />}

      {progress !== null && !loading && (
        <IndexProgress
          progress={progress}
          isLead={isLead}
          indexing={indexing}
          onIndex={() => void index()}
        />
      )}

      {loading && <p className="text-sm text-muted">{t("loading")}</p>}

      {!loading && indexed && results !== null && (
        <>
          <p className="text-xs font-medium uppercase tracking-wide text-muted">{t("results")}</p>
          {results.length === 0 ? (
            <p className="text-sm text-muted">{t("empty")}</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {results.map((r, i) => (
                <li key={`${r.article.id}-${i}`} className="rounded-lg border border-border bg-surface-muted p-3">
                  <Link
                    href={withSearch(`/projects/${projectId}/annotate?article=${r.article.id}`, searchId)}
                    className="text-sm font-medium text-primary hover:underline"
                  >
                    {r.article.title ?? t("untitled")}
                    {r.section_title ? <span className="font-normal text-muted"> — {r.section_title}</span> : null}
                    <span className="ml-2 text-xs text-muted">{r.score.toFixed(3)}</span>
                  </Link>
                  <p className="mt-1 line-clamp-3 text-sm text-foreground">{r.excerpt}</p>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
