"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/Button";

interface Step {
  n: number;
  title: string;
  desc: string;
  active: boolean;
  note?: string;
  warn?: boolean;
}

/**
 * 4-step onboarding for an empty project (spec v4, direction A): the KPIs,
 * the « Analyses » section and the accordions stay hidden, and the
 * « no model registered » warning sits in step 3, where it matters.
 */
export function OnboardingSteps({
  projectId,
  modelCount,
  isLead,
}: {
  projectId: string;
  modelCount: number | null;
  isLead: boolean;
}) {
  const t = useTranslations("project");
  const steps: Step[] = [
    { n: 1, title: t("onbStep1"), desc: t("onbStep1Desc"), active: true },
    { n: 2, title: t("onbStep2"), desc: t("onbStep2Desc"), note: t("onbAfterImport"), active: false },
    { n: 3, title: t("onbStep3"), desc: t("onbStep3Desc"), warn: modelCount === 0, active: false },
    { n: 4, title: t("onbStep4"), desc: t("onbStep4Desc"), active: false },
  ];
  return (
    <section className="flex flex-col gap-2" aria-label={t("onbStep1")}>
      {steps.map((s) => (
        <div
          key={s.n}
          className={`flex items-start gap-3 rounded-xl p-3 ${
            s.active ? "border-2 border-primary bg-primary/5" : "opacity-60"
          }`}
        >
          <span
            className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-bold text-white ${
              s.active ? "bg-primary" : "bg-border"
            }`}
            aria-hidden
          >
            {s.n}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-semibold text-foreground">{s.title}</p>
              {s.active && isLead && (
                <Link href={`/projects/${projectId}/import`}>
                  <Button size="sm">{t("onbCta")} →</Button>
                </Link>
              )}
            </div>
            <p className="mt-0.5 text-sm text-muted">{s.desc}</p>
            {s.note && <p className="mt-0.5 text-xs text-muted">{s.note}</p>}
            {s.warn && (
              <p className="mt-1 text-sm font-medium text-warning">⚠ {t("onbNoModel")}</p>
            )}
          </div>
        </div>
      ))}
    </section>
  );
}
