"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { getStorage, Storage } from "@/lib/api";
import { Card } from "@/components/ui/Card";

const gb = (n: number | null | undefined): string => (n == null ? "—" : `${(n / 1024 ** 3).toFixed(1)} GB`);
const mb = (n: number): string => (n >= 1024 ** 3 ? gb(n) : `${Math.round(n / 1024 ** 2)} MB`);

/** Espace disque du serveur, poids des PDF et projets inactifs (administrateur). */
export function StoragePanel() {
  const t = useTranslations("storage");
  const locale = useLocale();
  const [s, setS] = useState<Storage | null>(null);

  useEffect(() => {
    getStorage().then(setS).catch(() => setS(null));
  }, []);
  if (!s) return null;
  const d = s.disk;
  return (
    <Card title={t("title")}>
      <div className="flex flex-col gap-3 text-sm">
        <div>
          <div className="mb-1 flex flex-wrap justify-between gap-2">
            <span className="font-medium text-foreground">
              {t("disk", { percent: d.percent, free: gb(d.free), total: gb(d.total) })}
            </span>
            <span className="text-xs text-muted">{t("alertAt", { percent: d.alert_percent, gb: d.alert_free_gb })}</span>
          </div>
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-surface-muted"
            role="img"
            aria-label={`${d.percent} %`}
          >
            <div
              className={`h-full ${d.low ? "bg-danger" : d.percent > d.alert_percent - 15 ? "bg-warning" : "bg-success"}`}
              style={{ width: `${Math.min(100, d.percent)}%` }}
            />
          </div>
          <p className="mt-1 text-xs text-muted">
            {t("breakdown", { db: gb(s.db_bytes), pdf: gb(s.pdf_bytes) })}
          </p>
        </div>
        <p className="text-xs text-muted">
          {s.retention_days > 0
            ? t("retentionOn", { days: s.retention_days, grace: s.grace_days })
            : t("retentionOff")}
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted">
              <tr>
                <th className="py-1 pr-3 font-normal">{t("project")}</th>
                <th className="py-1 pr-3 text-right font-normal">{t("articles")}</th>
                <th className="py-1 pr-3 text-right font-normal">PDF</th>
                <th className="py-1 pr-3 text-right font-normal">{t("idle")}</th>
                <th className="py-1 font-normal">{t("deletion")}</th>
              </tr>
            </thead>
            <tbody>
              {s.projects.map((p) => (
                <tr key={p.id} className="border-t border-border/60">
                  <td className="max-w-[16rem] truncate py-1 pr-3">
                    <Link href={`/projects/${p.id}`} className="text-primary hover:underline">
                      {p.title}
                    </Link>
                  </td>
                  <td className="py-1 pr-3 text-right tabular-nums">{p.articles.toLocaleString()}</td>
                  <td className="py-1 pr-3 text-right tabular-nums">{mb(p.pdf_bytes)}</td>
                  <td className="py-1 pr-3 text-right tabular-nums">{t("days", { n: p.idle_days })}</td>
                  <td className={`py-1 ${p.delete_on ? "text-danger" : "text-muted"}`}>
                    {p.delete_on ? new Date(p.delete_on).toLocaleDateString(locale) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}
