"use client";

import { ReactNode, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui/Feedback";

/**
 * Auth guard: redirects to /login if the user is not signed in.
 * Shows a spinner while the current user is loading.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const t = useTranslations("common");

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  if (loading || !user) {
    return <Spinner label={t("loading")} />;
  }
  return <>{children}</>;
}
