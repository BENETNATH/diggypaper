import { ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "success" | "ghost";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
}

const VARIANTS: Record<string, string> = {
  primary:
    "bg-primary text-primary-foreground hover:bg-primary-hover disabled:bg-primary/50",
  secondary:
    "bg-surface text-foreground border border-border hover:bg-surface-muted disabled:opacity-50",
  danger: "bg-danger text-danger-foreground hover:opacity-90 disabled:opacity-50",
  // audit 2026-11-23 F-3: primary action "confirming" (annotation submit) —
  // contrastes validés 5.53:1 light / 7.86:1 dark (primary-foreground on success).
  success: "bg-success text-primary-foreground hover:opacity-90 disabled:opacity-50",
  ghost: "bg-transparent text-foreground hover:bg-surface-muted disabled:opacity-50",
};

// audit 2026-11-10 P2-9: touch targets ≥ 44 px on mobile (sm only; desktop keeps 32 px).
const SIZES: Record<string, string> = {
  sm: "min-h-[44px] px-3 py-1 text-sm sm:min-h-[32px]",
  md: "min-h-[44px] px-4 py-2 text-sm sm:min-h-[40px]",
  lg: "min-h-[48px] px-5 py-2.5 text-base",
};

/**
 * Consistent button (design-system: stable variant/size/disabled API).
 * Targets ≥ 32 px (AA), visible focus, 120ms ease-out motion, reduced-motion respected.
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
