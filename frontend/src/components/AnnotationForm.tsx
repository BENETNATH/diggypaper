"use client";

import { useId, useState } from "react";
import { useTranslations } from "next-intl";
import { SchemaField } from "@/lib/api";
import { AnswerTextarea, DecisionFields, defaultValues, Values } from "@/components/DecisionFields";
import { splitEvidence } from "@/lib/evidence";
import { quoteFound } from "@/lib/highlight";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";

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
  /** Article text: a quote absent from it is flagged « non trouvée » (P-4) and does not scroll. */
  text?: string;
}

/**
 * Formulaire d'annotation humaine (MODULE 5).
 *
 * Champs : use_ai_behavior, animal_sex, confidence_score, evidence_quotes,
 * reasoning_summary. Validation minimale (confidence ∈ [0,1]).
 */
export function AnnotationForm({ fields, onSubmit, onError, initial, quotes: given, onQuotesChange, onQuoteClick, text }: AnnotationFormProps) {
  const t = useTranslations("annotate");
  const [values, setValues] = useState<Values>(() => defaultValues(fields, initial));
  const [confidence, setConfidence] = useState((initial?.confidence_score as number) ?? 0.5);
  const confidenceId = useId();
  const [localQuotes, setLocalQuotes] = useState<string[]>(() => splitEvidence(initial?.evidence_quotes).all);
  const quotes = given ?? localQuotes;
  const setQuotes = (next: string[] | ((prev: string[]) => string[])) => {
    const value = typeof next === "function" ? next(quotes) : next;
    (onQuotesChange ?? setLocalQuotes)(value);
  };
  const [quoteInput, setQuoteInput] = useState("");
  const [reasoning, setReasoning] = useState((initial?.reasoning_summary as string) ?? "");
  // Per-field model quotes (dict shape, P-5): up to 3 clickable bullets under each field.
  const byField = splitEvidence(initial?.evidence_quotes).byField;
  const hasFieldQuotes = Object.keys(byField).length > 0;
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function addQuote() {
    const q = quoteInput.trim();
    if (!q) return;
    setQuotes((prev) => [...prev, q]);
    setQuoteInput("");
  }

  function removeQuote(index: number) {
    setQuotes((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return; // caveman-review 2026-11-10 pattern: double-click guard
    if (confidence < 0 || confidence > 1) {
      const msg = t("confidenceInvalid");
      setError(msg);
      onError?.(msg);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit({
        ...values,
        confidence_score: confidence,
        evidence_quotes: quotes,
        reasoning_summary: reasoning,
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <DecisionFields
        fields={fields}
        values={values}
        onChange={setValues}
        certainty={initial?.field_confidence as Record<string, number | null> | undefined}
        extra={
          hasFieldQuotes
            ? (f) => {
                const list = (byField[f.key] ?? []).slice(0, 3);
                if (list.length === 0) return null;
                return (
                  <ul className="mt-1.5 list-disc pl-5 text-xs">
                    {list.map((q, i) => (
                      <li key={i} className="leading-snug">
                        {onQuoteClick ? (
                          <button
                            type="button"
                            onClick={() => onQuoteClick(q)}
                            className="text-left text-primary underline-offset-2 hover:underline"
                          >
                            {q}
                          </button>
                        ) : (
                          <span className="text-foreground/70">{q}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                );
              }
            : undefined
        }
      />

      <div>
        <label htmlFor={confidenceId} className="block text-sm font-medium">
          {t("confidence")} ({confidence.toFixed(2)})
        </label>
        <input
          id={confidenceId}
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
          {/* audit 2026-11-10 P3-10: house Input + Button (was raw elements, off-system styling). */}
          <Input
            type="text"
            id="annotation-quote-input"
            value={quoteInput}
            onChange={(e) => setQuoteInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addQuote();
              }
            }}
            placeholder={t("quotePlaceholder")}
            aria-label={t("evidenceQuotes")}
            className="flex-1"
          />
          <Button type="button" size="sm" onClick={addQuote}>
            {t("addQuote")}
          </Button>
        </div>
        <ul className="mt-2 flex flex-col gap-1">
          {quotes.map((q, i) => {
            const found = !text || quoteFound(text, q);
            return (
              <li
                key={i}
                className="flex items-center justify-between rounded bg-surface-muted p-2"
              >
                {onQuoteClick ? (
                  <button
                    type="button"
                    onClick={() => found && onQuoteClick(q)}
                    className="min-w-0 flex-1 text-left text-sm text-foreground underline-offset-2 hover:text-primary hover:underline"
                    title={found ? t("jumpToQuote") : t("quoteNotFound")}
                  >
                    {q}
                  </button>
                ) : (
                  <span className="min-w-0 flex-1 text-sm">{q}</span>
                )}
                {!found && <span className="ml-2 shrink-0 text-xs text-muted">{t("quoteNotFound")}</span>}
                <button
                  type="button"
                  onClick={() => removeQuote(i)}
                  className="ml-2 shrink-0 text-danger"
                  aria-label={t("removeQuote")}
                >
                  ×
                </button>
              </li>
            );
          })}
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

      {/* audit 2026-11-23 F-3: house Button (was raw, off-system) + verrou pendant l'envoi. */}
      <Button type="submit" variant="success" loading={submitting}>
        {t("submitAnnotation")}
      </Button>
    </form>
  );
}
