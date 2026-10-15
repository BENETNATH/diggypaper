"use client";

import { RequireAuth } from "@/components/RequireAuth";
import { RequireAdmin } from "@/components/RequireAdmin";
import { SettingsPanel } from "@/components/SettingsPanel";

export default function SettingsPage() {
  return (
    <RequireAuth>
      <RequireAdmin>
        <SettingsPanel />
      </RequireAdmin>
    </RequireAuth>
  );
}
