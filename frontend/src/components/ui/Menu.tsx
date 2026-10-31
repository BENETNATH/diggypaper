"use client";

import { ReactNode, useEffect, useRef, useState } from "react";

/**
 * Dropdown menu: a button that opens a list of actions. Closes on outside click, on Escape and
 * after choosing an action (``closeOnClick``). Keyboard: Escape; the focus stays on the button.
 */
export function Menu({
  label,
  children,
  align = "right",
  closeOnClick = true,
  busy = false,
}: {
  label: ReactNode;
  children: ReactNode;
  align?: "left" | "right";
  closeOnClick?: boolean;
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div className="relative" ref={root}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex min-h-[32px] items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1 text-sm font-medium text-foreground transition-colors hover:bg-surface-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
      >
        {label}
        {busy && <span className="h-2 w-2 animate-pulse rounded-full bg-primary motion-reduce:animate-none" aria-hidden />}
        <span aria-hidden className="text-xs text-muted">▾</span>
      </button>
      {open && (
        <div
          role="menu"
          onClick={closeOnClick ? () => setOpen(false) : undefined}
          className={`absolute z-30 mt-1 min-w-[16rem] max-w-[22rem] rounded-lg border border-border bg-surface p-1 shadow-lg ${
            align === "right" ? "right-0" : "left-0"
          }`}
        >
          {children}
        </div>
      )}
    </div>
  );
}

const ITEM = "block w-full rounded-md px-3 py-2 text-left text-sm text-foreground hover:bg-surface-muted focus-visible:bg-surface-muted disabled:opacity-50";

export function MenuItem({
  onClick,
  href,
  download,
  disabled,
  children,
}: {
  onClick?: () => void;
  href?: string;
  download?: boolean | string;
  disabled?: boolean;
  children: ReactNode;
}) {
  if (href)
    return (
      <a role="menuitem" href={href} download={download} className={ITEM}>
        {children}
      </a>
    );
  return (
    <button type="button" role="menuitem" onClick={onClick} disabled={disabled} className={ITEM}>
      {children}
    </button>
  );
}

export function MenuNote({ children }: { children: ReactNode }) {
  return <div className="px-3 py-1.5 text-xs text-muted">{children}</div>;
}

export function MenuSeparator() {
  return <div role="separator" className="my-1 border-t border-border" />;
}
