import { ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
}

const VARIANTS: Record<string, string> = {
  primary:
    "bg-primary text-primary-foreground hover:bg-primary-hover disabled:bg-primary/50",
  secondary:
    "bg-surface text-foreground border border-border hover:bg-surface-muted disabled:opacity-50",
  danger: "bg-danger text-danger-foreground hover:opacity-90 disabled:opacity-50",
  ghost: "bg-transparent text-foreground hover:bg-surface-muted disabled:opacity-50",
};

const SIZES: Record<string, string> = {
  sm: "min-h-[32px] px-3 py-1 text-sm",
  md: "min-h-[40px] px-4 py-2 text-sm",
  lg: "min-h-[48px] px-5 py-2.5 text-base",
};

/**
 * Bouton cohérent (design-system : API variant/size/disabled stable).
 * Cibles ≥ 32 px (AA), focus visible, motion 120ms ease-out, reduced-motion respecté.
 */
export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  className = "",
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors duration-fast ease-out focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:cursor-not-allowed ${SIZES[size]} ${VARIANTS[variant]} ${className}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && (
        <span
          className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent motion-reduce:animate-none"
          aria-hidden
        />
      )}
      {children}
    </button>
  );
}
