import { getTranslations } from "next-intl/server";

const REPO = "https://github.com/BENETNATH/diggypaper";

export async function Footer() {
  const t = await getTranslations("footer");
  const link = "underline-offset-2 hover:text-foreground hover:underline";
  return (
    <footer className="border-t border-border px-4 py-4 pb-20 text-center text-xs text-muted md:pb-4">
      <p className="mx-auto flex max-w-6xl flex-wrap items-center justify-center gap-x-3 gap-y-1">
        <span>Diggypaper</span>
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
