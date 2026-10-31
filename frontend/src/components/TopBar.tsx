"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useTranslations } from "next-intl";

/** Top navigation bar (desktop). */
export function TopBar() {
  const t = useTranslations("nav");
  const { user, logout } = useAuth();

  return (
    <header className="sticky top-0 z-40 hidden border-b border-border bg-surface md:block">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <div className="flex items-center gap-6">
          <Link href="/projects" className="text-lg font-bold text-primary">
            {t("appName")}
          </Link>
          <nav className="flex items-center gap-4 text-sm">
            <Link href="/projects" className="text-muted hover:text-primary">
              {t("projects")}
            </Link>
            {user && (
              <Link href="/models" className="text-muted hover:text-primary">
                {t("models")}
              </Link>
            )}
            {user?.role === "ADMIN" && (
              <>
                <Link href="/admin/users" className="text-muted hover:text-primary">
                  {t("users")}
                </Link>
                <Link href="/settings" className="text-muted hover:text-primary">
                  {t("settings")}
                </Link>
              </>
            )}
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <LanguageSwitcher />
          {user ? (
            <>
              <Link href="/profile" className="text-muted hover:text-primary">
                {user.full_name || user.email}
              </Link>
              <button onClick={logout} className="text-muted hover:text-danger">
                {t("logout")}
              </button>
            </>
          ) : (
            <Link href="/login" className="text-muted hover:text-primary">
              {t("login")}
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
