import {
  InputHTMLAttributes,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
  LabelHTMLAttributes,
  ReactNode,
} from "react";

/**
 * Wrapper label + champ + aide + erreur (WCAG : association implicite via <label>,
 * erreur identifiée par texte ET icône — pas par la couleur seule, SC 1.4.1).
 */
export function Field({
  label,
  error,
  hint,
  children,
  ...rest
}: LabelHTMLAttributes<HTMLLabelElement> & {
  label: ReactNode;
  error?: string | null;
  hint?: string;
}) {
  return (
    <label className="block" {...rest}>
      <span className="mb-1 block text-sm font-medium text-foreground">{label}</span>
      {children}
      {hint && !error && (
        <span className="mt-1 block text-xs text-muted">{hint}</span>
      )}
      {error && (
        <span
          className="mt-1 flex items-center gap-1 text-sm text-danger"
          role="alert"
        >
          <span aria-hidden>⚠</span>
          {error}
        </span>
      )}
    </label>
  );
}

const inputClass =
  "w-full rounded-lg border border-border bg-surface px-3 py-2.5 text-sm text-foreground placeholder:text-muted transition-colors duration-fast ease-out focus:border-primary focus:outline-none focus:ring-2 focus:ring-focus/40";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputClass} ${props.className ?? ""}`} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`${inputClass} ${props.className ?? ""}`} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${inputClass} ${props.className ?? ""}`} />;
}

export function Checkbox({
  label,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return (
    <label className="flex items-center gap-2">
      <input
        type="checkbox"
        {...rest}
        className="h-5 w-5 rounded border-border bg-surface text-primary focus:ring-2 focus:ring-focus/40"
      />
      <span className="text-sm text-foreground">{label}</span>
    </label>
  );
}
