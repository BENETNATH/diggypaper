"use client";

import { useTranslations } from "next-intl";
import { shortVersion } from "@/lib/modelVersion";

/** Traced model: name (id if different) · version frozen at analysis time. */
export function ModelRef({
  name,
  identifier,
  version,
}: {
  name: string;
  identifier?: string | null;
  version?: string | null;
}) {
  const t = useTranslations("common");
  const short = shortVersion(version);
  return (
    <span>
      {name}
      {identifier && identifier !== name && <span className="text-muted"> ({identifier})</span>} ·{" "}
      {short ? (
        <>
          {t("modelVersion")}{" "}
          {short !== version ? (
            // abbreviated form: full value in the title, focusable by keyboard
            <abbr title={version ?? undefined} tabIndex={0}>
              {short}
            </abbr>
          ) : (
            short
          )}
        </>
      ) : (
        <span className="italic">{t("versionNotRecorded")}</span>
      )}
    </span>
  );
}
