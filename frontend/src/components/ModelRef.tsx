"use client";

import { useTranslations } from "next-intl";
import { shortVersion } from "@/lib/modelVersion";

/** Modèle tracé : nom (identifiant si différent) · version figée au moment de l'analyse. */
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
            // forme abrégée : valeur complète dans le title, focalisable au clavier
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
