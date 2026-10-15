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

/** Cookie lu par next-intl (cf. i18n.ts) : la langue d'interface suit le navigateur. */
export const LOCALE_COOKIE = "DP_LOCALE";

export function readLocaleCookie(): string | null {
  const m = document.cookie.match(new RegExp(`(?:^|; )${LOCALE_COOKIE}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

export function writeLocaleCookie(lang: string): void {
  document.cookie = `${LOCALE_COOKIE}=${lang}; path=/; max-age=31536000; samesite=lax`;
}

/** Sélecteur de langue de la barre de navigation : change l'interface et, si connecté, le profil. */
export function LanguageSwitcher({ className = "" }: { className?: string }) {
  const locale = useLocale();
  const { user } = useAuth();

  async function change(lang: string) {
    writeLocaleCookie(lang);
    if (user) {
      try {
        await updateProfile({ language_preference: lang });
      } catch {
        /* préférence non enregistrée : la langue reste appliquée via le cookie */
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
