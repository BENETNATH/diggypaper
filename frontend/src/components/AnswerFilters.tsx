"use client";

import { useTranslations } from "next-intl";
import { SchemaField } from "@/lib/api";

export type Answers = Record<string, string>;

const selectCls = "rounded border border-border bg-surface p-1 text-xs text-foreground";

/** Filtres « réponse à l'item X = valeur » (liste d'articles, sélecteur d'articles d'une recherche). */
export function AnswerFilters({
  fields,
  values,
  onChange,
}: {
  fields: SchemaField[];
  values: Answers;
  onChange: (key: string, value: string) => void;
}) {
  const t = useTranslations("articles");
  return (
    <>
      {fields.map((f) => (
        <label key={f.key} className="flex flex-col gap-0.5 text-xs text-muted">
          {f.label}
          {f.type === "text" ? (
            <input
              type="search"
              value={values[f.key] ?? ""}
              placeholder={t("containsText")}
              onChange={(e) => onChange(f.key, e.target.value)}
              className={`${selectCls} w-40`}
            />
          ) : (
            <select value={values[f.key] ?? ""} onChange={(e) => onChange(f.key, e.target.value)} className={selectCls}>
              <option value="">{t("anyAnswer")}</option>
              {f.type === "boolean" ? (
                <>
                  <option value="true">{t("yes")}</option>
                  <option value="false">{t("no")}</option>
                </>
              ) : (
                f.options?.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))
              )}
            </select>
          )}
        </label>
      ))}
    </>
  );
}
