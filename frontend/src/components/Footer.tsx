import { getTranslations } from "next-intl/server";

const REPO = "https://github.com/BENETNATH/diggypaper";

/**
 * The app version (semver), fetched from the backend /version endpoint.
 * Server-side (the footer is a server component): the backend origin comes from
 * API_ORIGIN (compose: http://backend:8000), falling back to the local dev backend.
 * A failure (backend down) must not break the footer — fall back to a neutral label.
 */
async function getVersion(): Promise<string | null> {
  const origin = process.env.API_ORIGIN ?? "http://localhost:8000";
  try {
    const res = await fetch(`${origin}/api/v1/version`, { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as { version?: string };
    return data.version ?? null;
  } catch {
    return null;
  }
}

export async function Footer() {
  const t = await getTranslations("footer");
  const version = await getVersion();
  const link = "underline-offset-2 hover:text-foreground hover:underline";
  return (
    <footer className="border-t border-border px-4 py-4 pb-20 text-center text-xs text-muted md:pb-4">
      <p className="mx-auto flex max-w-6xl flex-wrap items-center justify-center gap-x-3 gap-y-1">
        <span>Diggypaper</span>
        {version && <span className="font-mono text-[0.7rem]">{version}</span>}
        <a href={REPO} target="_blank" rel="noopener noreferrer" className={link}>
          {t("source")}
        </a>
        <a href={`${REPO}/blob/main/LICENSE`} target="_blank" rel="noopener noreferrer" className={link}>
          {t("license")}
        </a>
        <span>{t("credit")}</span>
      </p>
    </footer>
  );
}
