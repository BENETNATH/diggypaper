"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import type { SearchInfo } from "@/lib/api";

/**
 * Barre de pastilles : toutes les analyses du projet — standards d'abord (ordre
 * de la chaîne A, B, C…), puis hors chaîne avec badge de type. C'est l'unique
 * sélecteur d'analyse (spec clarte-1 R1) : le point coloré dit si l'analyse a
 * des résultats (vert) ou non (neutre) ; la pastille active (bordure primaire,
 * `aria-pressed`) est le seul pointeur ; cliquer recentre l'application sur
 * elle. « + Nouvelle » (pointillés) crée une analyse (spec v4).
 */
export function SearchPills({
  searches,
  activeId,
  onPick,
  ariaLabel,
  label,
  newHref,
}: {
  /** Toutes les analyses, standards d'abord (ordre de la chaîne), puis hors chaîne. */
  searches: SearchInfo[];
  activeId?: string;
  onPick: (searchId: string) => void;
  ariaLabel: string;
  /** Libellé de la rangée (« ANALYSES ») ; omis si absent. */
  label?: string;
  /** Lien « + Nouvelle » (affiché uniquement pour un lead). */
  newHref?: string;
}) {
  const t = useTranslations("project");
  if (searches.length === 0 && !newHref) return null;
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={ariaLabel}>
      {label && (
        <span className="text-xs font-bold uppercase tracking-wider text-muted">{label}</span>
      )}
      {searches.map((s, i) => {
        const active = s.id === activeId;
        const letter = String.fromCharCode(65 + Math.min(i, 25));
        const done = s.predicted > 0 || s.reviewed > 0;
        return (
          <button
            key={s.id}
            type="button"
            aria-pressed={active}
            title={s.question ?? s.name}
            onClick={() => onPick(s.id)}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm transition-colors ${
              active
                ? "border-primary bg-primary/10 font-semibold text-primary"
                : "border-border bg-surface text-muted hover:text-foreground"
            }`}
          >
            <span
              data-testid="dot"
              className={`h-2 w-2 rounded-full ${done ? "bg-success" : "bg-muted"}`}
              aria-hidden
            />
            {letter} · {s.name}
            {s.kind !== "standard" && (
              <span className="text-xs font-normal text-muted">{t(`kind_${s.kind}`)}</span>
            )}
          </button>
        );
      })}
      {newHref && (
        <Link
          href={newHref}
          className="rounded-full border border-dashed border-border px-3 py-1 text-sm text-muted transition-colors hover:text-foreground"
        >
          + {t("pillNew")}
        </Link>
      )}
    </div>
  );
}
