"use client";

import { ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui/Feedback";

/** Admin guard: shows a message if the user is not ADMIN (the backend enforces it too). */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading || !user) {
    return <Spinner label="Chargement…" />;
  }
  if (user.role !== "ADMIN") {
    return (
      <div className="p-4 text-sm text-muted">
        Accès réservé aux administrateurs.
      </div>
    );
  }
  return <>{children}</>;
}
