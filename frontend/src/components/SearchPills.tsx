"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import type { SearchInfo } from "@/lib/api";

/**
 * Barre de pastilles : tous les corpus de questions du projet (spec 2026-11-20-analyses-runs-2
 * R1). Le point coloré dit si le corpus a des résultats (vert) ou non (neutre) ;
 * « brouillon » suffixe un corpus sans aucun run ; la pastille active (bordure
 * primaire, `aria-pressed`) est le seul pointeur ; cliquer recentre l'application
 * sur elle. « + Nouveau corpus de questions » (pointillés) crée un corpus.
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
  const ts = useTranslations("searches");
  if (searches.length === 0 && !newHref) return null;
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={ariaLabel}>
      {label && (
        <span className="text-xs font-bold uppercase tracking-wider text-muted">{label}</span>
      )}
      {searches.map((s) => {
        const active = s.id === activeId;
        const done = s.predicted > 0 || s.reviewed > 0;
        return (
          <button
            key={s.id}
            type="button"
            aria-pressed={active}
            title={s.question ?? s.name}
            onClick={() => onPick(s.id)}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm transition-colors duration-fast ease-out ${
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
            {s.name}
            {s.last_job == null && (
              <span className="text-xs font-normal text-muted">{ts("draft")}</span>
            )}
          </button>
        );
      })}
      {newHref && (
        <Link
          href={newHref}
          className="rounded-full border border-dashed border-border px-3 py-1 text-sm text-muted transition-colors duration-fast ease-out hover:text-foreground"
        >
          + {t("pillNew")}
        </Link>
      )}
    </div>
  );
}
