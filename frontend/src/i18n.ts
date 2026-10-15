import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";

export const locales = ["fr", "en", "de", "es"] as const;
// Langue par défaut de l'instance : DEFAULT_LANGUAGE (.env), lue à chaque requête côté serveur.
const envLocale = (process.env.DEFAULT_LANGUAGE ?? "fr").toLowerCase();
export const defaultLocale = (locales as readonly string[]).includes(envLocale) ? envLocale : "fr";

export default getRequestConfig(async ({ requestLocale }) => {
  // Langue : cookie DP_LOCALE (posé uniquement quand l'utilisateur choisit sa langue dans le profil), sinon français.
  let locale = (await requestLocale) ?? cookies().get("DP_LOCALE")?.value;

  if (!locale || !locales.includes(locale as (typeof locales)[number])) {
    locale = defaultLocale;
  }

  return {
    locale,
    messages: (await import(`./messages/${locale}.json`)).default,
  };
});
