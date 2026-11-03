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

/**
 * CI state of the running commit (green/red/unknown), fetched from the backend
 * /ci-status endpoint. Unknown in dev (no build SHA) — the badge is hidden then.
 */
async function getCiState(): Promise<string | null> {
  const origin = process.env.API_ORIGIN ?? "http://localhost:8000";
  try {
    const res = await fetch(`${origin}/api/v1/ci-status`, { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as { state?: string };
    return data.state ?? null;
  } catch {
    return null;
  }
}

// Only green/red are shown; "unknown" (dev, in progress, backend down) hides the badge.
const CI_BADGE: Record<string, { cls: string; label: string }> = {
  green: { cls: "bg-success/15 text-success", label: "CI ✓" },
  red: { cls: "bg-danger/15 text-danger", label: "CI ✗" },
};

export async function Footer() {
  const t = await getTranslations("footer");
  const version = await getVersion();
  const ciState = await getCiState();
  const link = "underline-offset-2 hover:text-foreground hover:underline";
  const ci = ciState ? CI_BADGE[ciState] : null;
  return (
    <footer className="border-t border-border px-4 py-4 pb-20 text-center text-xs text-muted md:pb-4">
      <p className="mx-auto flex max-w-6xl flex-wrap items-center justify-center gap-x-3 gap-y-1">
        <span>Diggypaper</span>
        {version && <span className="font-mono text-[0.7rem]">{version}</span>}
        {ci && (
          <span
            className={`rounded-full px-1.5 py-0.5 text-[0.65rem] font-medium ${ci.cls}`}
            title={t("ciState")}
          >
            {ci.label}
          </span>
        )}
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
