"use client";

import { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui/Feedback";

/** Admin guard: shows a message if the user is not ADMIN (the backend enforces it too). */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const t = useTranslations("common");
  const { user, loading } = useAuth();
  if (loading || !user) {
    return <Spinner label={t("loading")} />;
  }
  if (user.role !== "ADMIN") {
    return (
      <div className="p-4 text-sm text-muted">
        {t("adminOnly")}
      </div>
    );
  }
  return <>{children}</>;
}
