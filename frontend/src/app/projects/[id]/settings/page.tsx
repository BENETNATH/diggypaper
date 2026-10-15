"use client";

import { useParams } from "next/navigation";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireAdmin } from "@/components/RequireAdmin";
import { SettingsPanel } from "@/components/SettingsPanel";

export default function ProjectSettingsPage() {
  const params = useParams<{ id: string }>();
  return (
    <RequireAuth>
      <RequireAdmin>
        <SettingsPanel projectId={params.id} />
      </RequireAdmin>
    </RequireAuth>
  );
}
