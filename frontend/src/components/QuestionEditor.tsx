"use client";

import { useTranslations } from "next-intl";
import { RankMetric, SchemaField } from "@/lib/api";
import { RANK_METRICS } from "@/lib/benchmark";
import { Button } from "@/components/ui/Button";
import { Checkbox, Field, Input, Select, Textarea } from "@/components/ui/Field";

const SECTION_KINDS = [
  "abstract",
  "introduction",
  "methods",
  "results",
  "discussion",
  "conclusion",
  "other",
];

const CHOICE_METRICS: RankMetric[] = ["accuracy"];

const slug = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^(\d)/, "f_$1")
    .slice(0, 40);

/** Éditeur contrôlé : question, champs de réponse, parties de texte lues par les modèles. */
export function QuestionEditor({
  question,
  fields,
  sections,
  onChange,
}: {
  question: string;
  fields: SchemaField[];
  sections: string[];
  onChange: (next: {
    question: string;
    fields: SchemaField[];
    sections: string[];
  }) => void;
}) {
  const t = useTranslations("question");
  const ti = useTranslations("import");
  const set = (
    patch: Partial<{ question: string; fields: SchemaField[]; sections: string[] }>
  ) => onChange({ question, fields, sections, ...patch });

  function patchField(i: number, patch: Partial<SchemaField> & { optionsText?: string }) {
    set({
      fields: fields.map((f, j) => {
        if (j !== i) return f;
        const { optionsText, ...rest } = patch;
        const next = { ...f, ...rest };
        if (optionsText !== undefined) next.options = optionsText.split(",").map((o) => o.trim());
        if (rest.label !== undefined && f.key.startsWith("new_")) next.key = slug(rest.label) || f.key;
        if (rest.type === "choice" && !next.options?.length) next.options = ["YES", "NO"];
        // Un item à choix n'a que l'exactitude (pas de rappel / précision).
        if (next.type === "choice" && next.primary) next.metric = "accuracy";
        if (next.type === "text") {
          delete next.primary; // le critère de classement est un item booléen ou à choix
          delete next.metric;
        }
        return next;
      }),
    });
  }

  /** Un seul critère de classement : le choisir retire l'ancien ; `null` n'en garde aucun. */
  function setPrimary(i: number | null) {
    set({
      fields: fields.map((f, j) => {
        const { primary, metric, ...rest } = f;
        void primary;
        return j === i ? { ...rest, primary: true, metric: f.type === "choice" ? "accuracy" : (metric ?? "recall") } : rest;
      }),
    });
  }

  function toggleSection(k: string) {
    set({ sections: sections.includes(k) ? sections.filter((x) => x !== k) : [...sections, k] });
  }

  return (
    <div className="flex flex-col gap-5">
      <Field label={t("questionLabel")} hint={t("questionHint")}>
        <Textarea rows={2} value={question} onChange={(e) => set({ question: e.target.value })} />
      </Field>

      <div>
        <p className="text-sm font-medium text-foreground">{t("fieldsTitle")}</p>
        <p className="mb-2 text-xs text-muted">{t("fieldsHint")}</p>
        <div className="flex flex-col gap-3">
          {fields.map((f, i) => (
            <div key={i} className="grid gap-3 rounded-lg border border-border p-3 sm:grid-cols-[1fr_9rem_auto]">
              <Field label={t("fieldLabel")}>
                <Input value={f.label} onChange={(e) => patchField(i, { label: e.target.value })} />
              </Field>
              <Field label={t("fieldType")}>
                <Select value={f.type} onChange={(e) => patchField(i, { type: e.target.value as SchemaField["type"] })}>
                  <option value="boolean">{t("typeBoolean")}</option>
                  <option value="choice">{t("typeChoice")}</option>
                  <option value="text">{t("typeText")}</option>
                </Select>
              </Field>
              <div className="flex items-end">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => set({ fields: fields.filter((_, j) => j !== i) })}
                  disabled={fields.length <= 1}
                >
                  {t("remove")}
                </Button>
              </div>
              {f.type !== "text" && (
                <div className="flex flex-wrap items-center gap-3 text-sm sm:col-span-3">
                  <label className="flex items-center gap-2 text-foreground">
                    <input
                      type="radio"
                      name="primary-criterion"
                      className="h-4 w-4 border-border text-primary focus:ring-2 focus:ring-focus/40"
                      checked={!!f.primary}
                      onChange={() => setPrimary(i)}
                    />
                    {t("primaryLabel")}
                  </label>
                  {f.primary && (
                    <>
                      <Select
                        className="!w-auto !py-1"
                        aria-label={t("metricLabel")}
                        value={f.metric ?? "recall"}
                        onChange={(e) => patchField(i, { metric: e.target.value as RankMetric })}
                      >
                        {(f.type === "choice" ? CHOICE_METRICS : RANK_METRICS).map((m) => (
                          <option key={m} value={m}>
                            {t(`metric_${m}`)}
                          </option>
                        ))}
                      </Select>
                      <Button type="button" variant="ghost" size="sm" onClick={() => setPrimary(null)}>
                        {t("primaryClear")}
                      </Button>
                    </>
                  )}
                  <span className="basis-full text-xs text-muted">{t("primaryHint")}</span>
                </div>
              )}
              {f.type === "choice" && (
                <div className="sm:col-span-3">
                  <Field label={t("options")} hint={t("optionsHint")}>
                    <Input
                      value={(f.options ?? []).join(", ")}
                      onChange={(e) => patchField(i, { optionsText: e.target.value })}
                    />
                  </Field>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="mt-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() =>
              set({ fields: [...fields, { key: `new_${fields.length + 1}`, label: "", type: "boolean" }] })
            }
          >
            + {t("addField")}
          </Button>
        </div>
      </div>

      <div>
        <p className="text-sm font-medium text-foreground">{t("textTitle")}</p>
        <p className="mb-2 text-xs text-muted">{t("textHint")}</p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Checkbox label={ti("kind_all")} checked={sections.includes("all")} onChange={() => toggleSection("all")} />
          {SECTION_KINDS.map((k) => (
            <Checkbox
              key={k}
              label={ti(`kind_${k}`)}
              checked={sections.includes(k) || sections.includes("all")}
              disabled={sections.includes("all")}
              onChange={() => toggleSection(k)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
