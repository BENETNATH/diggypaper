"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui/Feedback";

/**
 * Landing (racine) : splash de marque + redirection selon l'état d'auth.
 * - Non connecté → /login
 * - Connecté → /projects
 */
export default function Home() {
  const t = useTranslations("landing");
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    router.replace(user ? "/projects" : "/login");
  }, [loading, user, router]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <span className="text-3xl font-bold text-primary">{t("appName")}</span>
        <span className="max-w-md text-sm text-muted">{t("tagline")}</span>
      </div>
      <Spinner label={t("loading")} />
    </div>
  );
}
