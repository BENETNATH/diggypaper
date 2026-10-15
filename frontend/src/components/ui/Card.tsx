import { ReactNode } from "react";

/** Carte de contenu (tokens sémantiques, bordure + padding). */
export function Card({
  title,
  children,
  className = "",
}: {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-border bg-surface p-4 ${className}`}
    >
      {title && (
        <h2 className="mb-3 text-base font-semibold text-foreground">{title}</h2>
      )}
      {children}
    </section>
  );
}

const BADGE_COLORS: Record<string, string> = {
  gray: "bg-surface-muted text-muted-foreground",
  green: "bg-success/15 text-success",
  red: "bg-danger/15 text-danger",
  blue: "bg-primary/15 text-primary",
  yellow: "bg-warning/20 text-warning",
};

/**
 * Badge de statut. Le texte est porteur du sens (SC 1.4.1) ; la couleur est
 * complémentaire, jamais l'indicateur unique.
 */
export function Badge({
  color = "gray",
  children,
}: {
  color?: keyof typeof BADGE_COLORS;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${BADGE_COLORS[color]}`}
    >
      {children}
    </span>
  );
}

/** Couleur de badge selon un statut de job/projet. */
export function statusColor(status: string): keyof typeof BADGE_COLORS {
  switch (status) {
    case "COMPLETED":
    case "FROZEN":
    case "AUTO_ACCEPTED":
    case "VALIDATED":
      return "green";
    case "FAILED":
    case "AUTO_REJECTED":
      return "red";
    case "RUNNING":
    case "PROCESSING":
    case "PENDING":
      return "blue";
    case "NEEDS_HUMAN_REVIEW":
    case "DRAFT":
      return "yellow";
    default:
      return "gray";
  }
}
