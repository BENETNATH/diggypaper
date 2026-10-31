"use client";

import { useLocale } from "next-intl";
import { useAuth } from "@/lib/auth";
import { updateProfile } from "@/lib/api";

const LANGS: { code: string; label: string }[] = [
  { code: "fr", label: "Français" },
  { code: "en", label: "English" },
  { code: "de", label: "Deutsch" },
  { code: "es", label: "Español" },
];

/** Cookie read by next-intl (cf. i18n.ts): the interface language follows the browser. */
export const LOCALE_COOKIE = "DP_LOCALE";

export function readLocaleCookie(): string | null {
  const m = document.cookie.match(new RegExp(`(?:^|; )${LOCALE_COOKIE}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

export function writeLocaleCookie(lang: string): void {
  document.cookie = `${LOCALE_COOKIE}=${lang}; path=/; max-age=31536000; samesite=lax`;
}

/** Language selector of the navigation bar: changes the interface and, if signed in, the profile. */
export function LanguageSwitcher({ className = "" }: { className?: string }) {
  const locale = useLocale();
  const { user } = useAuth();

  async function change(lang: string) {
    writeLocaleCookie(lang);
    if (user) {
      try {
        await updateProfile({ language_preference: lang });
      } catch {
        /* preference not saved: the language stays applied via the cookie */
      }
    }
    window.location.reload();
  }

  return (
    <select
      aria-label="Language"
      value={locale}
      onChange={(e) => void change(e.target.value)}
      className={`rounded border border-border bg-surface px-1 py-0.5 text-xs text-foreground ${className}`}
    >
      {LANGS.map((l) => (
        <option key={l.code} value={l.code}>
          {l.label}
        </option>
      ))}
    </select>
  );
}
