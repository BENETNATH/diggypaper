"use client";

import { ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui/Feedback";

/** Garde admin : affiche un message si l'utilisateur n'est pas ADMIN (le backend l'applique aussi). */
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
