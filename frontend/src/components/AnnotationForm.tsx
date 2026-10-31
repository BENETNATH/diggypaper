"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { SchemaField } from "@/lib/api";
import { AnswerTextarea, DecisionFields, defaultValues, Values } from "@/components/DecisionFields";

export type Annotation = Record<string, unknown> & {
  confidence_score: number;
  evidence_quotes: string[];
  reasoning_summary: string;
};

interface AnnotationFormProps {
  /** Fields of the project's question. */
  fields: SchemaField[];
  /** Called with the full annotation on submission. */
  onSubmit: (annotation: Annotation) => void;
  /** Called on error (e.g. invalid submission). */
  onError?: (message: string) => void;
  /** Initial values (prefill from a prediction). */
  initial?: Record<string, unknown>;
  /** Evidence excerpts driven by the page (highlighting and "+" on the text); otherwise local state. */
  quotes?: string[];
  onQuotesChange?: (quotes: string[]) => void;
  /** Click on an excerpt: the page reveals it in the document (scroll to the highlight). */
  onQuoteClick?: (quote: string) => void;
}

/**
 * Formulaire d'annotation humaine (MODULE 5).
 *
 * Champs : use_ai_behavior, animal_sex, confidence_score, evidence_quotes,
 * reasoning_summary. Validation minimale (confidence ∈ [0,1]).
 */
export function AnnotationForm({ fields, onSubmit, onError, initial, quotes: given, onQuotesChange, onQuoteClick }: AnnotationFormProps) {
  const t = useTranslations("annotate");
  const [values, setValues] = useState<Values>(() => defaultValues(fields, initial));
  const [confidence, setConfidence] = useState((initial?.confidence_score as number) ?? 0.5);
  const [localQuotes, setLocalQuotes] = useState<string[]>((initial?.evidence_quotes as string[]) ?? []);
  const quotes = given ?? localQuotes;
  const setQuotes = (next: string[] | ((prev: string[]) => string[])) => {
    const value = typeof next === "function" ? next(quotes) : next;
    (onQuotesChange ?? setLocalQuotes)(value);
  };
  const [quoteInput, setQuoteInput] = useState("");
  const [reasoning, setReasoning] = useState((initial?.reasoning_summary as string) ?? "");
  const [error, setError] = useState<string | null>(null);

  function addQuote() {
    const q = quoteInput.trim();
    if (!q) return;
    setQuotes((prev) => [...prev, q]);
    setQuoteInput("");
  }

  function removeQuote(index: number) {
    setQuotes((prev) => prev.filter((_, i) => i !== index));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (confidence < 0 || confidence > 1) {
      const msg = t("confidenceInvalid");
      setError(msg);
      onError?.(msg);
      return;
    }
    setError(null);
    onSubmit({
      ...values,
      confidence_score: confidence,
      evidence_quotes: quotes,
      reasoning_summary: reasoning,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <DecisionFields
        fields={fields}
        values={values}
        onChange={setValues}
        certainty={initial?.field_confidence as Record<string, number | null> | undefined}
      />

      <div>
        <label className="block text-sm font-medium">
          {t("confidence")} ({confidence.toFixed(2)})
        </label>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={confidence}
          onChange={(e) => setConfidence(parseFloat(e.target.value))}
          className="mt-1 w-full"
        />
      </div>

      <div>
        <label className="block text-sm font-medium">{t("evidenceQuotes")}</label>
        <div className="mt-1 flex gap-2">
          <input
            type="text"
            value={quoteInput}
            onChange={(e) => setQuoteInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addQuote();
              }
            }}
            placeholder={t("quotePlaceholder")}
            className="flex-1 rounded border p-2"
          />
          <button
            type="button"
            onClick={addQuote}
            className="rounded bg-primary px-3 py-2 text-white"
          >
            {t("addQuote")}
          </button>
        </div>
        <ul className="mt-2 flex flex-col gap-1">
          {quotes.map((q, i) => (
            <li
              key={i}
              className="flex items-center justify-between rounded bg-surface-muted p-2"
            >
              {onQuoteClick ? (
                <button
                  type="button"
                  onClick={() => onQuoteClick(q)}
                  className="min-w-0 flex-1 text-left text-sm text-foreground underline-offset-2 hover:text-primary hover:underline"
                  title={t("jumpToQuote")}
                >
                  {q}
                </button>
              ) : (
                <span className="min-w-0 flex-1 text-sm">{q}</span>
              )}
              <button
                type="button"
                onClick={() => removeQuote(i)}
                className="ml-2 shrink-0 text-danger"
                aria-label={t("removeQuote")}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <label className="block text-sm font-medium">{t("reasoningSummary")}</label>
        <div className="mt-1">
          <AnswerTextarea
            value={reasoning}
            onChange={setReasoning}
          />
        </div>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      <button type="submit" className="rounded bg-success px-4 py-2 text-white">
        {t("submitAnnotation")}
      </button>
    </form>
  );
}
