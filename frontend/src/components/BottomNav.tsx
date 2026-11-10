"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useTranslations } from "next-intl";

/**
 * Bottom navigation bar (mobile). spec clarte-2 R4: ≤ 4 items, no logout (the tap-through
 * logout lived on the profile page); the « Connexion » link stays for anonymous visitors.
 */
export function BottomNav() {
  const t = useTranslations("nav");
  const { user } = useAuth();
  const pathname = usePathname();

  const items: { href: string; label: string; show: boolean }[] = [
    { href: "/projects", label: t("projects"), show: true },
    { href: "/models", label: t("models"), show: !!user },
    { href: "/profile", label: t("profile"), show: true },
    { href: "/admin/users", label: t("admin"), show: user?.role === "ADMIN" },
  ];

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 flex overflow-x-auto border-t border-border bg-surface md:hidden">
      {items
        .filter((i) => i.show)
        .map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`flex-1 whitespace-nowrap px-3 py-3 text-center text-sm ${
                active ? "font-semibold text-primary" : "text-muted"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      <div className="flex flex-1 shrink-0 items-center justify-center px-2">
        <LanguageSwitcher />
      </div>
      {!user && (
        <Link href="/login" className="flex-1 whitespace-nowrap px-3 py-3 text-center text-sm text-muted">
          {t("login")}
        </Link>
      )}
    </nav>
  );
}
