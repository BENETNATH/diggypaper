import { useCallback } from "react";
import { useTranslations } from "next-intl";
import { errorKey } from "@/lib/api";

/**
 * Translated message of a raw API error ("errors" namespace, via errorKey);
 * unknown error → raw message. Use in catch blocks that show errors to the user.
 */
export function useApiError() {
  // Root namespace: errorKey() returns full keys ("errors.leadRequired").
  const t = useTranslations("");
  return useCallback(
    (e: unknown): string => {
      const raw = typeof e === "string" ? e : e instanceof Error ? e.message : "error";
      const key = errorKey(raw);
      return key ? t(key) : raw;
    },
    [t]
  );
}
