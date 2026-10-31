import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";

export const locales = ["fr", "en", "de", "es"] as const;
// Instance default language: DEFAULT_LANGUAGE (.env), read on every server-side request.
const envLocale = (process.env.DEFAULT_LANGUAGE ?? "fr").toLowerCase();
export const defaultLocale = (locales as readonly string[]).includes(envLocale) ? envLocale : "fr";

export default getRequestConfig(async ({ requestLocale }) => {
  // Language: DP_LOCALE cookie (set only when the user picks a language in the profile), otherwise French.
  let locale = (await requestLocale) ?? cookies().get("DP_LOCALE")?.value;

  if (!locale || !locales.includes(locale as (typeof locales)[number])) {
    locale = defaultLocale;
  }

  return {
    locale,
    messages: (await import(`./messages/${locale}.json`)).default,
  };
});
