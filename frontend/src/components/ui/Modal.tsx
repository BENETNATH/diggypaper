"use client";

import { ReactNode, useEffect, useRef } from "react";
import { useTranslations } from "next-intl";

/** Focusable controls inside the dialog (focus trap, WCAG 2.1.2). */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Modal window: darkened backdrop, Escape / outside click / button to close, internal scrolling.
    audit 2026-11-10 P2-1: focus is trapped while open and restored to the trigger on close. */
export function Modal({
  title,
  onClose,
  children,
  wide = false,
  closeLabel,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  closeLabel?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const t = useTranslations("common");
  // Latest onClose in a ref (stable keydown listener); updated in an effect —
  // ref writes during render are forbidden (react-hooks/refs).
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const panelEl = panel.current;
    panelEl?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab" || !panelEl) return;
      const focusables = Array.from(panelEl.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === panelEl)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-overlay p-4 sm:items-center"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`w-full ${wide ? "max-w-4xl" : "max-w-lg"} rounded-xl border border-border bg-surface shadow-xl outline-none`}
      >
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <h2 className="text-base font-semibold text-foreground">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={closeLabel ?? t("close")}
            className="rounded-md px-2 py-1 text-lg leading-none text-muted hover:bg-surface-muted hover:text-foreground"
          >
            ×
          </button>
        </div>
        <div className="max-h-[75vh] overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  );
}
