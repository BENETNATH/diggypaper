"use client";

import { useTranslations } from "next-intl";
import { Select } from "@/components/ui/Field";

export interface TriagePolicyValue {
  accept: number | null;
  reject: number | null;
}

/** Project triage presets (spec 2026-11-09-triage-seuils R1) — free values are not
 * allowed in v1; "désactivé" = family disabled (null). */
export const ACCEPT_PRESETS = [0.85, 0.7, 0.9];
export const REJECT_PRESETS = [0.15, 0.1, 0.25];

export function TriageThresholds({
  value,
  onChange,
}: {
  /** Current value; a missing policy displays the defaults (0.85 / 0.15). */
  value: TriagePolicyValue;
  onChange: (next: TriagePolicyValue) => void;
}) {
  const t = useTranslations("inference");
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium text-foreground" htmlFor="triage-accept">
          {t("thresholdsAccept")}
        </label>
        <Select
          id="triage-accept"
          value={String(value.accept ?? 0.85)}
          onChange={(e) =>
            onChange({
              accept: e.target.value === "" ? null : Number(e.target.value),
              reject: value.reject,
            })
          }
        >
          {ACCEPT_PRESETS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
          <option value="">{t("thresholdsDisabled")}</option>
        </Select>
      </div>
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium text-foreground" htmlFor="triage-reject">
          {t("thresholdsReject")}
        </label>
        <Select
          id="triage-reject"
          value={String(value.reject ?? 0.15)}
          onChange={(e) =>
            onChange({
              accept: value.accept,
              reject: e.target.value === "" ? null : Number(e.target.value),
            })
          }
        >
          {REJECT_PRESETS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
          <option value="">{t("thresholdsDisabled")}</option>
        </Select>
      </div>
    </div>
  );
}
