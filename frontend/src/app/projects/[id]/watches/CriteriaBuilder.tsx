"use client";

import { useTranslations } from "next-intl";
import type { SchemaField, WatchCriterion } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";

/**
 * Builder of the watch alert criteria (spec 2026-11-08-criteres-veille):
 * rows of field / free-question criteria (expected answer + optional
 * confidence window) combined by AND (default) or OR. Controlled component:
 * the parent owns the state via `onChange(criteria, combine)`.
 */
export default function CriteriaBuilder({
  fields,
  criteria,
  combine,
  onChange,
}: {
  fields: SchemaField[];
  criteria: WatchCriterion[];
  combine: "AND" | "OR";
  onChange: (criteria: WatchCriterion[], combine: "AND" | "OR") => void;
}) {
  const t = useTranslations("watches");
  // Only boolean / choice fields are eligible (text matching is unreliable).
  const eligible = fields.filter((f) => f.type === "boolean" || f.type === "choice");

  function updateAt(i: number, row: WatchCriterion) {
    onChange(criteria.map((c, j) => (j === i ? row : c)), combine);
  }

  function add() {
    onChange(
      [...criteria, { kind: "field", field_key: eligible[0]?.key ?? "", expected: true }],
      combine
    );
  }

  function remove(i: number) {
    onChange(criteria.filter((_, j) => j !== i), combine);
  }

  function switchKind(i: number, kind: "field" | "free") {
    // Changing the kind resets the row (the other kind's data is stale).
    if (kind === "free") {
      updateAt(i, { kind: "free", question: "", expected: true });
    } else {
      updateAt(i, { kind: "field", field_key: eligible[0]?.key ?? "", expected: true });
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-foreground">{t("criteria")}</span>
        <span className="text-xs text-muted">{t("combineHint")}</span>
        {combine === "AND" ? (
          <Button type="button" size="sm" variant="secondary" onClick={() => onChange(criteria, "OR")}>
            {t("combineOr")}
          </Button>
        ) : (
          <Button type="button" size="sm" variant="secondary" onClick={() => onChange(criteria, "AND")}>
            {t("combineAnd")}
          </Button>
        )}
      </div>

      {criteria.map((c, i) => {
        const field =
          c.kind === "field" ? eligible.find((f) => f.key === c.field_key) : undefined;
        return (
          <div key={i} className="flex flex-wrap items-end gap-2 rounded-lg border border-border bg-surface p-2">
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("critKind")}
              <Select
                value={c.kind}
                aria-label={t("critKind")}
                onChange={(e) => switchKind(i, e.target.value as "field" | "free")}
              >
                <option value="field">{t("critKindField")}</option>
                <option value="free">{t("critKindFree")}</option>
              </Select>
            </label>

            {c.kind === "free" ? (
              <label className="flex flex-col gap-1 text-xs text-muted">
                {t("critQuestion")}
                <Input
                  value={c.question ?? ""}
                  placeholder={t("critQuestion")}
                  maxLength={500}
                  onChange={(e) => updateAt(i, { ...c, question: e.target.value })}
                />
              </label>
            ) : eligible.length > 0 && (
              <>
                <label className="flex flex-col gap-1 text-xs text-muted">
                  {t("critField")}
                  <Select
                    value={c.field_key ?? ""}
                    aria-label={t("critField")}
                    onChange={(e) => {
                      const f = eligible.find((x) => x.key === e.target.value);
                      updateAt(i, {
                        ...c,
                        field_key: e.target.value,
                        expected: f?.type === "choice" ? (f.options?.[0] ?? "") : true,
                      });
                    }}
                  >
                    {eligible.map((f) => (
                      <option key={f.key} value={f.key}>
                        {f.label}
                      </option>
                    ))}
                  </Select>
                </label>
                {field && field.type === "boolean" && (
                  <AnswerSelect value={String(c.expected)} onAnswer={(v) => updateAt(i, { ...c, expected: v })} t={t} />
                )}
                {field && field.type === "choice" && (
                  <label className="flex flex-col gap-1 text-xs text-muted">
                    {t("critExpected")}
                    <Select
                      value={String(c.expected)}
                      aria-label={t("critExpected")}
                      onChange={(e) => updateAt(i, { ...c, expected: e.target.value })}
                    >
                      {(field.options ?? []).map((o) => (
                        <option key={o} value={o}>
                          {o}
                        </option>
                      ))}
                    </Select>
                  </label>
                )}
              </>
            )}

            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("critConfMin")}
              <Input
                type="number"
                step="0.05"
                min="0"
                max="1"
                aria-label={t("critConfMin")}
                value={c.conf_min ?? ""}
                onChange={(e) =>
                  updateAt(i, { ...c, conf_min: e.target.value === "" ? null : Number(e.target.value) })
                }
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted">
              {t("critConfMax")}
              <Input
                type="number"
                step="0.05"
                min="0"
                max="1"
                aria-label={t("critConfMax")}
                value={c.conf_max ?? ""}
                onChange={(e) =>
                  updateAt(i, { ...c, conf_max: e.target.value === "" ? null : Number(e.target.value) })
                }
              />
            </label>
            <Button type="button" size="sm" variant="danger" aria-label={t("critRemove")} onClick={() => remove(i)}>
              ✕
            </Button>
          </div>
        );
      })}

      <Button type="button" size="sm" variant="secondary" onClick={add}>
        {t("criteriaAdd")}
      </Button>
    </div>
  );
}

/** Yes/No answer selector shared by boolean fields and free questions. */
function AnswerSelect({
  value,
  onAnswer,
  t,
}: {
  value: string;
  onAnswer: (bool: boolean) => void;
  t: (key: string) => string;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-muted">
      {t("critExpected")}
      <Select value={value} aria-label={t("critExpected")} onChange={(e) => onAnswer(e.target.value === "true")}>
        <option value="true">{t("critYes")}</option>
        <option value="false">{t("critNo")}</option>
      </Select>
    </label>
  );
}
