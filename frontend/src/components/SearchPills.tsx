"use client";

import type { SearchInfo } from "@/lib/api";

/**
 * Barre de pastilles : les analyses standards (la chaîne). La pastille bleue est
 * l'analyse active ; cliquer recentre l'application sur elle (spec v4 R6).
 */
export function SearchPills({
  searches,
  activeId,
  onPick,
  ariaLabel,
}: {
  /** Les analyses standards, dans l'ordre de la chaîne. */
  searches: SearchInfo[];
  activeId?: string;
  onPick: (searchId: string) => void;
  ariaLabel: string;
}) {
  if (searches.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={ariaLabel}>
      {searches.map((s) => {
        const active = s.id === activeId;
        return (
          <button
            key={s.id}
            type="button"
            aria-pressed={active}
            title={s.question ?? s.name}
            onClick={() => onPick(s.id)}
            className={`rounded-full border px-3 py-1 text-sm transition-colors ${
              active
                ? "border-primary bg-primary/10 font-semibold text-primary"
                : "border-border bg-surface text-muted hover:text-foreground"
            }`}
          >
            {s.name}
          </button>
        );
      })}
    </div>
  );
}
