"use client";

import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { SchemaField } from "@/lib/api";
import { Field, Select, Checkbox, Textarea } from "@/components/ui/Field";
import { Certainty } from "@/components/Certainty";

export type Values = Record<string, unknown>;

/**
 * Champ texte ajusté à sa hauteur : les réponses du modèle peuvent faire plusieurs
 * lignes ; un simple `<input>` les tronquait (illisibles en entier).
 */
export function AnswerTextarea({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 320)}px`; // ~11 lignes, puis défilement interne
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

/** Valeurs par défaut d'un jeu de champs (booléen=false, choix=1re option, texte=""). */
export function defaultValues(fields: SchemaField[], initial?: Values): Values {
  const out: Values = {};
  for (const f of fields) {
    out[f.key] =
      initial?.[f.key] ??
      (f.type === "boolean" ? false : f.type === "choice" ? f.options?.[0] ?? "" : "");
  }
  return out;
}

/** Rend un champ par SchemaField de la question du projet. */
export function DecisionFields({
  fields,
  values,
  onChange,
  certainty,
}: {
  fields: SchemaField[];
  values: Values;
  onChange: (next: Values) => void;
  /** Certitude du modèle pour chaque réponse (clé du champ → 0–1). */
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
