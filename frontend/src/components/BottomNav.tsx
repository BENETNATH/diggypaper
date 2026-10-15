"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useTranslations } from "next-intl";

/** Barre de navigation inférieure (mobile). */
export function BottomNav() {
  const t = useTranslations("nav");
  const { user, logout } = useAuth();
  const pathname = usePathname();

  const items: { href: string; label: string; show: boolean }[] = [
    { href: "/projects", label: t("projects"), show: true },
    { href: "/profile", label: t("profile"), show: true },
    { href: "/models", label: t("models"), show: !!user },
    { href: "/admin/users", label: t("users"), show: user?.role === "ADMIN" },
    { href: "/settings", label: t("settings"), show: user?.role === "ADMIN" },
  ];

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 flex border-t border-border bg-surface md:hidden">
      {items
        .filter((i) => i.show)
        .map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex-1 py-3 text-center text-sm ${
                active ? "font-semibold text-primary" : "text-muted"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      <div className="flex flex-1 items-center justify-center">
        <LanguageSwitcher />
      </div>
      {user ? (
        <button onClick={logout} className="flex-1 py-3 text-center text-sm text-muted">
          {t("logout")}
        </button>
      ) : (
        <Link href="/login" className="flex-1 py-3 text-center text-sm text-muted">
          {t("login")}
        </Link>
      )}
    </nav>
  );
}
