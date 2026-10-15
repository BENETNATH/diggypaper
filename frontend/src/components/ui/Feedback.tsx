import { ReactNode } from "react";

/** État vide : raison + prochaine action (frontend-design-pro). */
export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-surface-muted/40 p-8 text-center">
      <p className="text-sm font-medium text-foreground">{title}</p>
      {hint && <p className="max-w-sm text-sm text-muted">{hint}</p>}
      {action}
    </div>
  );
}

/** Bannière d'erreur lisible : icône + texte (pas couleur seule), role=alert. */
export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      className="flex items-center justify-between gap-3 rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger"
      role="alert"
    >
      <span className="flex items-center gap-2">
        <span aria-hidden>⚠</span>
        {message}
      </span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="shrink-0 font-medium underline underline-offset-2 hover:no-underline"
        >
          Réessayer
        </button>
      )}
    </div>
  );
}

/**
 * Skeleton de chargement (frontend-design-pro : skeleton > spinner).
 * `rows` = nombre de lignes simulées ; `height` = hauteur d'une ligne.
 */
export function Skeleton({
  rows = 3,
  height = "h-4",
  className = "",
}: {
  rows?: number;
  height?: string;
  className?: string;
}) {
  return (
    <div className={`flex flex-col gap-3 ${className}`} aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className={`skeleton rounded ${height} w-full`} />
      ))}
    </div>
  );
}

/** Spinner de chargement (usage ponctuel ; skeleton de préférence pour les listes). */
export function Spinner({ label }: { label?: string }) {
  return (
    <div
      className="flex items-center justify-center gap-3 p-8 text-sm text-muted"
      role="status"
    >
      <span className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent motion-reduce:animate-none" />
      {label}
    </div>
  );
}

/** Toast de succès (auto-dismiss, role=status). */
export function Toast({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss: () => void;
}) {
  return (
    <div
      className="fixed bottom-20 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-foreground px-4 py-2.5 text-sm text-background shadow-lg md:bottom-6"
      role="status"
    >
      {message}
      <button
        onClick={onDismiss}
        className="ml-3 opacity-70 hover:opacity-100"
        aria-label="Fermer"
      >
        ×
      </button>
    </div>
  );
}

/** Barre de progression (valeur connue) ou indéterminée (value=null). */
export function ProgressBar({ value, label }: { value: number | null; label?: string }) {
  const pct = value === null ? null : Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div>
      {label && <p className="mb-1 text-xs text-foreground">{label}</p>}
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-surface-muted"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct ?? undefined}
      >
        <div
          className={`h-full rounded-full bg-primary transition-all ${
            pct === null ? "w-1/3 animate-pulse" : ""
          }`}
          style={pct === null ? undefined : { width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
