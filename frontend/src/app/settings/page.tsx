"use client";

import { RequireAuth } from "@/components/RequireAuth";
import { RequireAdmin } from "@/components/RequireAdmin";
import { SettingsPanel } from "@/components/SettingsPanel";
import { StoragePanel } from "@/components/StoragePanel";

export default function SettingsPage() {
  return (
    <RequireAuth>
      <RequireAdmin>
        <div className="flex flex-col gap-4">
          <StoragePanel />
          <SettingsPanel />
        </div>
      </RequireAdmin>
    </RequireAuth>
  );
}
