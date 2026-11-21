"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

/** Copy to the clipboard, with a legacy fallback when the Clipboard API is
 * absent (non-secure context: HTTP on a non-localhost IP). */
async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "absolute";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  if (!ok) throw new Error("copy refused");
}

/** One-click copy to the clipboard (spec 2026-11-21-liste-depot-copie R8): the icon
 * flips to a check and a local tooltip shows for 2 s — no global toast. Allowed
 * for every role (clipboard read, not a project action, R13).
 */
export function CopyButton({
  value,
  label,
  title,
  size = "lg",
  className = "",
}: {
  /** Text to copy (article title, DOI string). */
  value: string;
  /** aria-label of the button (also the hover tooltip). */
  label: string;
  /** Short noun for the success message, e.g. "Titre" / "DOI". */
  title?: string;
  size?: "lg" | "sm";
  className?: string;
}) {
  const tc = useTranslations("common");
  const ta = useTranslations("articles");
  const [state, setState] = useState<"idle" | "ok" | "error">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const what = title ?? label;
  const box = size === "lg" ? "h-[26px] w-[26px] rounded-md" : "h-5 w-5 rounded";
  const icon = size === "lg" ? "h-[15px] w-[15px]" : "h-3 w-3";

  function copy(e: React.MouseEvent) {
    e.preventDefault(); // the row is a link: don't navigate
    e.stopPropagation();
    void copyText(value)
      .then(() => setState("ok"))
      .catch(() => setState("error"));
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 2000);
  }

  const msg = state === "ok" ? tc("copied", { what }) : ta("bulkCopyFailed");
  return (
    <span className={`relative inline-flex ${className}`}>
      <button
        type="button"
        aria-label={label}
        title={label}
        onClick={copy}
        className={`inline-flex cursor-pointer items-center justify-center border-0 transition-colors duration-fast ease-out ${box} ${
          state === "ok"
            ? "bg-success/10 text-success"
            : state === "error"
              ? "bg-danger/10 text-danger"
              : "bg-transparent text-muted hover:bg-surface-muted hover:text-foreground"
        }`}
      >
        {state === "ok" ? (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" className={icon} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={icon} aria-hidden>
            <rect x="9" y="9" width="11" height="11" rx="2" />
            <path d="M5 15V6a2 2 0 0 1 2-2h9" />
          </svg>
        )}
      </button>
      {state !== "idle" && (
        <span
          aria-hidden
          className={`absolute -top-8 left-1/2 z-20 -translate-x-1/2 rounded-md px-2 py-1 text-[11.5px] whitespace-nowrap ${
            state === "ok" ? "bg-foreground text-background" : "bg-danger text-background"
          }`}
        >
          {msg}
        </span>
      )}
      <span className="sr-only" aria-live="polite">
        {state === "idle" ? "" : msg}
      </span>
    </span>
  );
}
