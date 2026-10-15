"use client";

import { ReactNode, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui/Feedback";

/**
 * Garde d'authentification : redirige vers /login si l'user n'est pas connecté.
 * Affiche un spinner pendant le chargement de l'user courant.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  if (loading || !user) {
    return <Spinner label="Chargement…" />;
  }
  return <>{children}</>;
}
