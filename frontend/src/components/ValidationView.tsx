"use client";

import { useTranslations } from "next-intl";
import type { PrimaryCriterion, Prf, SchemaField, Scored } from "@/lib/api";
import { pct, primaryParts } from "@/lib/benchmark";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-surface-muted p-2 text-center">
      <div className="text-lg font-bold text-foreground">{value}</div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  );
}

/** Matrice de confusion de la pertinence : lignes = référence (gold), colonnes = modèle. */
function Confusion({ m }: { m: Prf }) {
  const t = useTranslations("validation");
  const cell = "px-3 py-1.5 text-center tabular-nums";
  return (
    <div className="overflow-x-auto">
      <table className="text-sm">
        <caption className="mb-1 text-left text-xs text-muted">{t("confusionTitle")}</caption>
        <thead>
          <tr className="text-xs text-muted">
            <th className="px-3 py-1 text-left font-normal">{t("goldVsModel")}</th>
            <th className="px-3 py-1 font-normal">{t("predRelevant")}</th>
            <th className="px-3 py-1 font-normal">{t("predNotRelevant")}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th className="px-3 py-1.5 text-left font-medium text-foreground">{t("goldRelevant")}</th>
            <td className={`${cell} bg-success/10`}>{m.tp}</td>
            <td className={`${cell} bg-danger/10`}>{m.fn}</td>
          </tr>
          <tr>
            <th className="px-3 py-1.5 text-left font-medium text-foreground">{t("goldNotRelevant")}</th>
            <td className={`${cell} bg-danger/10`}>{m.fp}</td>
            <td className={`${cell} bg-success/10`}>{m.tn}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/**
 * Notes d'un modèle comparées au gold : critère principal, pertinence (rappel / précision / F1),
 * tableau par item et matrice de confusion. Sert au benchmark et à la validation d'une analyse.
 */
export function ValidationView({
  scored,
  primary,
  fields,
}: {
  scored: Scored;
  primary?: PrimaryCriterion;
  fields: SchemaField[];
}) {
  const t = useTranslations("validation");
  const rel = scored.relevance;
  const crit = primaryParts(primary, fields);
  const items = Object.entries(scored.items ?? {});
  return (
    <div className="flex flex-col gap-3">
      {crit && primary && (
        <p className="text-sm text-foreground">
          {t("primary")} :{" "}
          <b>
            {crit.item ?? t("relevance")} · {t(`metric_${crit.metric}`)} = {pct(primary.value)}
          </b>
        </p>
      )}
      <p className="text-xs text-muted">{t("scoredOn", { n: scored.n })}</p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label={`${t("relevance")} · ${t("metric_recall")}`} value={pct(rel.recall)} />
        <Stat label={`${t("relevance")} · ${t("metric_precision")}`} value={pct(rel.precision)} />
        <Stat label={`${t("relevance")} · ${t("metric_f1")}`} value={pct(rel.f1)} />
        <Stat label={t("metric_accuracy")} value={pct(scored.accuracy ?? rel.accuracy)} />
      </div>
      {items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="mb-1 text-left text-xs text-muted">{t("itemsTitle")}</caption>
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted">
                <th className="py-1 pr-3 font-normal">{t("item")}</th>
                <th className="px-2 py-1 text-right font-normal">{t("n")}</th>
                <th className="px-2 py-1 text-right font-normal">{t("metric_recall")}</th>
                <th className="px-2 py-1 text-right font-normal">{t("metric_precision")}</th>
                <th className="px-2 py-1 text-right font-normal">{t("metric_f1")}</th>
                <th className="px-2 py-1 text-right font-normal">{t("metric_accuracy")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map(([key, m]) => (
                <tr key={key} className="border-b border-border/50">
                  <td className="py-1 pr-3 text-foreground">
                    {fields.find((f) => f.key === key)?.label ?? key}
                    {primary?.item === key && <span className="ml-1 text-primary">★</span>}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums">{m.n}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{pct(m.recall)}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{pct(m.precision)}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{pct(m.f1)}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{pct(m.accuracy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Confusion m={rel} />
    </div>
  );
}
