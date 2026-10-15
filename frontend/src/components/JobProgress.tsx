"use client";

import { ReactNode, useEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { etaSeconds, formatDuration, pushSample, ratePerSecond, Sample } from "@/lib/progress";

export type Segments = { accepted: number; rejected: number; review: number };

/**
 * Avancement d'une tâche, sobre : une ligne d'information (n / total, %, temps restant, vitesse),
 * une fine barre — découpée en validés / écartés / à relire quand on les connaît —, une légende.
 * `inline` : une seule ligne très compacte pour l'en-tête du projet ; sinon version détaillée.
 */
export function JobProgress({
  done,
  total,
  segments,
  title,
  action,
  hint,
  inline = false,
}: {
  done: number;
  total: number;
  segments?: Segments;
  title?: ReactNode;
  action?: ReactNode;
  hint?: ReactNode;
  /** Une seule ligne très compacte (en-tête de projet) : texte, mini-jauge, actions. */
  inline?: boolean;
}) {
  const t = useTranslations("progress");
  const locale = useLocale();
  const [samples, setSamples] = useState<Sample[]>([]);
  const last = useRef(done);

  useEffect(() => {
    last.current = done;
    setSamples((s) => pushSample(s, Date.now(), done));
  }, [done]);
  // Le temps passe même sans nouvelle donnée : on rafraîchit l'estimation toutes les 5 s.
  useEffect(() => {
    const id = setInterval(() => setSamples((s) => pushSample(s, Date.now(), last.current)), 5000);
    return () => clearInterval(id);
  }, []);

  const known = total > 0;
  const pct = known ? Math.min(100, (done / total) * 100) : null;
  const rate = ratePerSecond(samples);
  const eta = etaSeconds(done, total, rate);
  const perMin = rate === null ? null : rate * 60;
  const seg = segments && done > 0 ? segments : null;
  const part = (n: number) => (known ? Math.min(100, (n / total) * 100) : 0);

  if (inline) {
    return (
      <div className="flex items-center gap-2 text-xs" role="status">
        {title && <span className="font-medium text-foreground">{title}</span>}
        <span className="font-medium tabular-nums text-foreground">
          {known ? `${done.toLocaleString(locale)}/${total.toLocaleString(locale)}` : t("starting")}
        </span>
        {pct !== null && <span className="tabular-nums text-muted">{Math.round(pct)} %</span>}
        {eta !== null && <span className="hidden text-muted sm:inline">· {t("eta", { time: formatDuration(eta) })}</span>}
        <div
          className="flex h-1.5 w-20 overflow-hidden rounded-full bg-surface-muted sm:w-28"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct === null ? undefined : Math.round(pct)}
        >
          {pct === null ? (
            <div className="h-full w-1/3 animate-pulse rounded-full bg-primary motion-reduce:animate-none" />
          ) : seg ? (
            <>
              <div className="h-full bg-success transition-all" style={{ width: `${part(seg.accepted)}%` }} />
              <div className="h-full bg-warning transition-all" style={{ width: `${part(seg.review)}%` }} />
              <div className="h-full bg-danger transition-all" style={{ width: `${part(seg.rejected)}%` }} />
            </>
          ) : (
            <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
          )}
        </div>
        {action}
      </div>
    );
  }

  const info = (
    <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm">
      {title && <span className="font-medium text-foreground">{title}</span>}
      <span className="font-medium tabular-nums text-foreground">
        {known ? `${done.toLocaleString(locale)} / ${total.toLocaleString(locale)}` : t("starting")}
      </span>
      {pct !== null && <span className="tabular-nums text-muted">{Math.round(pct)} %</span>}
      {eta !== null && <span className="text-muted">· {t("eta", { time: formatDuration(eta) })}</span>}
      {perMin !== null && (
        <span className="text-xs text-muted">
          · {t("rate", { n: perMin.toLocaleString(locale, { maximumFractionDigits: perMin < 10 ? 1 : 0 }) })}
        </span>
      )}
    </span>
  );

  const bar = (
    <div
      className={`flex w-full overflow-hidden rounded-full bg-surface-muted h-1.5`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct === null ? undefined : Math.round(pct)}
    >
      {pct === null ? (
        <div className="h-full w-1/3 animate-pulse rounded-full bg-primary motion-reduce:animate-none" />
      ) : seg ? (
        <>
          <div className="h-full bg-success transition-all" style={{ width: `${part(seg.accepted)}%` }} />
          <div className="h-full bg-warning transition-all" style={{ width: `${part(seg.review)}%` }} />
          <div className="h-full bg-danger transition-all" style={{ width: `${part(seg.rejected)}%` }} />
        </>
      ) : (
        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
      )}
    </div>
  );

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-3">
        {info}
        {action}
      </div>
      {bar}
      {seg && (
        <p className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted">
          <span>
            <span className="mr-1 inline-block h-2 w-2 rounded-full bg-success align-middle" />
            {t("accepted", { n: seg.accepted.toLocaleString(locale) })}
          </span>
          <span>
            <span className="mr-1 inline-block h-2 w-2 rounded-full bg-warning align-middle" />
            {t("review", { n: seg.review.toLocaleString(locale) })}
          </span>
          <span>
            <span className="mr-1 inline-block h-2 w-2 rounded-full bg-danger align-middle" />
            {t("rejected", { n: seg.rejected.toLocaleString(locale) })}
          </span>
        </p>
      )}
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}
