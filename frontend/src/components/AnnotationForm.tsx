"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { SchemaField } from "@/lib/api";
import { DecisionFields, defaultValues, Values } from "@/components/DecisionFields";

export type Annotation = Record<string, unknown> & {
  confidence_score: number;
  evidence_quotes: string[];
  reasoning_summary: string;
};

interface AnnotationFormProps {
  /** Champs de la question du projet. */
  fields: SchemaField[];
  /** Appelé avec l'annotation complète à la soumission. */
  onSubmit: (annotation: Annotation) => void;
  /** Appelé en cas d'erreur (ex. soumission invalide). */
  onError?: (message: string) => void;
  /** Valeurs initiales (pré-remplissage depuis une prédiction). */
  initial?: Record<string, unknown>;
}

/**
 * Formulaire d'annotation humaine (MODULE 5).
 *
 * Champs : use_ai_behavior, animal_sex, confidence_score, evidence_quotes,
 * reasoning_summary. Validation minimale (confidence ∈ [0,1]).
 */
export function AnnotationForm({ fields, onSubmit, onError, initial }: AnnotationFormProps) {
  const t = useTranslations("annotate");
  const [values, setValues] = useState<Values>(() => defaultValues(fields, initial));
  const [confidence, setConfidence] = useState((initial?.confidence_score as number) ?? 0.5);
  const [quotes, setQuotes] = useState<string[]>((initial?.evidence_quotes as string[]) ?? []);
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
              <span className="text-sm">{q}</span>
              <button
                type="button"
                onClick={() => removeQuote(i)}
                className="text-danger"
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
        <textarea
          value={reasoning}
          onChange={(e) => setReasoning(e.target.value)}
          rows={3}
          className="mt-1 w-full rounded border p-2"
        />
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      <button type="submit" className="rounded bg-success px-4 py-2 text-white">
        {t("submitAnnotation")}
      </button>
    </form>
  );
}
