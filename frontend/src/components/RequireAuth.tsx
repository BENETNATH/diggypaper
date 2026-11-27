"use client";

import { ReactNode, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { loginRedirectHref } from "@/lib/api";
import { ErrorBanner, Spinner } from "@/components/ui/Feedback";

/**
 * Auth guard: redirects to /login?next=<here> if the user is not signed in.
 * Shows a spinner while the current user is loading, and an error + retry when
 * the session check failed for another reason than 401 (no redirect loop).
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading, error, refresh } = useAuth();
  const router = useRouter();
  const t = useTranslations("common");
  const te = useTranslations("errors");

  useEffect(() => {
    if (!loading && !user && !error) {
      router.replace(loginRedirectHref(window.location.pathname, window.location.search));
    }
  }, [loading, user, error, router]);

  if (!loading && !user && error) {
    return <ErrorBanner message={te("generic")} onRetry={() => void refresh()} />;
  }
  if (loading || !user) {
    return <Spinner label={t("loading")} />;
  }
  return <>{children}</>;
}
