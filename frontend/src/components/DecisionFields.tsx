"use client";

import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { SchemaField } from "@/lib/api";
import { Field, Select, Checkbox, Textarea } from "@/components/ui/Field";
import { Certainty } from "@/components/Certainty";

export type Values = Record<string, unknown>;

/**
 * Text field auto-sized to its height: the model's answers can span several
 * lines; a plain `<input>` truncated them (unreadable in full).
 */
export function AnswerTextarea({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 320)}px`; // ~11 lines, then internal scrolling
  }, [value]);
  return (
    <Textarea
      ref={ref}
      rows={2}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="leading-snug"
    />
  );
}

/** Default values of a field set (boolean=false, choice=1st option, text=""). */
export function defaultValues(fields: SchemaField[], initial?: Values): Values {
  const out: Values = {};
  for (const f of fields) {
    out[f.key] =
      initial?.[f.key] ??
      (f.type === "boolean" ? false : f.type === "choice" ? f.options?.[0] ?? "" : "");
  }
  return out;
}

/** Renders one field per SchemaField of the project's question. */
export function DecisionFields({
  fields,
  values,
  onChange,
  certainty,
}: {
  fields: SchemaField[];
  values: Values;
  onChange: (next: Values) => void;
  /** Model certainty for each answer (field key → 0–1). */
  certainty?: Record<string, number | null>;
}) {
  const tSex = useTranslations("sex");
  const t = useTranslations("annotate");
  const set = (key: string, v: unknown) => onChange({ ...values, [key]: v });

  const renderField = (f: SchemaField) =>
    f.type === "boolean" ? (
      <Checkbox label={f.label} checked={Boolean(values[f.key])} onChange={(e) => set(f.key, e.target.checked)} />
    ) : f.type === "choice" ? (
      <Field label={f.label}>
        <Select value={String(values[f.key] ?? "")} onChange={(e) => set(f.key, e.target.value)}>
          {f.options?.map((o) => (
            <option key={o} value={o}>
              {tSex.has(o) ? tSex(o) : o}
            </option>
          ))}
        </Select>
      </Field>
    ) : (
      <Field label={f.label}>
        <AnswerTextarea value={String(values[f.key] ?? "")} onChange={(v) => set(f.key, v)} />
      </Field>
    );

  return (
    <div className="flex flex-col gap-4">
      {fields.map((f) => (
        <div key={f.key}>
          {certainty?.[f.key] != null && (
            <div className="mb-1 flex items-center gap-2 text-xs text-muted">
              {t("modelCertainty")} <Certainty value={certainty[f.key]} />
            </div>
          )}
          {renderField(f)}
        </div>
      ))}
    </div>
  );
}
