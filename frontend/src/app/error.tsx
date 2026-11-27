"use client";

import { useEffect } from "react";
import { useTranslations } from "next-intl";
import { ErrorBanner } from "@/components/ui/Feedback";

/** Route error boundary: translated message + retry (renders inside the root layout). */
export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  const t = useTranslations("errors");
  useEffect(() => {
    console.error(error);
  }, [error]);
  return <ErrorBanner message={t("generic")} onRetry={retry} />;
}
