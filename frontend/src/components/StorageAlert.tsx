"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { getStorageStatus, DiskStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/** Banner for the administrator when the server disk is almost full. */
export function StorageAlert() {
  const t = useTranslations("storage");
  const { user } = useAuth();
  const [d, setD] = useState<DiskStatus | null>(null);
  const admin = user?.role === "ADMIN";

  useEffect(() => {
    if (admin) getStorageStatus().then(setD).catch(() => setD(null));
  }, [admin]);
  if (!admin || !d?.low) return null;
  return (
    <div role="alert" className="border-b border-danger/40 bg-danger/10 px-4 py-2 text-center text-sm text-danger">
      {t("alert", { percent: d.percent, free: (d.free / 1024 ** 3).toFixed(1) })}{" "}
      <Link href="/settings" className="font-medium underline">
        {t("manage")}
      </Link>
    </div>
  );
}
