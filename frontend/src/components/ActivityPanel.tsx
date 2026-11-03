"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  ActivityAction,
  getAdminActivity,
  getProjectActivity,
} from "@/lib/api";

/**
 * §3 — Recent actions (imports / analyses / benchmarks) with the user who
 * launched them. Two modes:
 *  - project: `projectId` set — the project's recent actions (members);
 *  - global:  `global` set    — the whole instance, with the project title (admin).
 *
 * Rendered collapsed inside a <details> by the pages (like « Comment ça marche »).
 */
export function ActivityPanel({
  projectId,
  global = false,
}: {
  projectId?: string;
  global?: boolean;
}) {
  const t = useTranslations("activity");
  const tj = useTranslations("jobs");
  const locale = useLocale();
  const [actions, setActions] = useState<ActivityAction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = global
      ? getAdminActivity()
      : getProjectActivity(projectId as string);
    load
      .then((d) => {
        if (!alive) return;
        setActions(d.actions);
        setError(null);
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : "error");
      });
    return () => {
      alive = false;
    };
  }, [global, projectId, reload]);

  if (error) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm text-danger">
        <span>{t("error")}</span>
        <button
          type="button"
          onClick={() => setReload((n) => n + 1)}
          className="text-primary underline"
        >
          {t("retry")}
        </button>
      </div>
    );
  }
  if (actions === null) {
    return <p className="text-sm text-muted">{t("loading")}</p>;
  }
  if (actions.length === 0) {
    return <p className="text-sm text-muted">{t("empty")}</p>;
  }

  return (
    <ul className="flex flex-col gap-1.5">
      {actions.map((a) => (
        <li key={a.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm text-foreground">
          <span className="font-medium">{t(a.type)}</span>
          {a.detail && (
            <code className="max-w-full truncate text-xs text-muted" title={a.detail}>
              {a.detail}
            </code>
          )}
          {global && a.project_title && (
            <span className="text-xs text-muted">· {a.project_title}</span>
          )}
          <span className="text-xs text-muted">
            · {a.user ?? t("unknownUser")}
          </span>
          <span
            className="text-xs text-muted"
            title={a.started_at ? new Date(a.started_at).toISOString() : undefined}
          >
            · {tj(`status_${a.status}`)}
            {a.started_at && <> · {new Date(a.started_at).toLocaleString(locale)}</>}
          </span>
        </li>
      ))}
    </ul>
  );
}
