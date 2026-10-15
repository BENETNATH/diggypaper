"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { listSearches, SearchInfo } from "@/lib/api";
import { currentSearch, resolveSearchId, searchStoreKey } from "@/lib/search";

const SEARCHES_EVENT = "diggy:searches";
/** Prévient les `useSearches` montés (sélecteur du layout) que la liste des recherches a changé. */
export const notifySearchesChanged = (): void => {
  window.dispatchEvent(new Event(SEARCHES_EVENT));
};

function readStored(projectId: string): string | null {
  try {
    return typeof window === "undefined" ? null : sessionStorage.getItem(searchStoreKey(projectId));
  } catch {
    return null; // stockage indisponible : recherche initiale
  }
}

export function storeSearchId(projectId: string, searchId: string): void {
  try {
    sessionStorage.setItem(searchStoreKey(projectId), searchId);
  } catch {
    /* stockage indisponible : la sélection ne survit qu'à travers l'URL */
  }
}

/**
 * Recherche courante du projet : `?search=` dans l'URL, sinon mémoire de l'onglet, sinon
 * `undefined` (= recherche initiale). Les pages client ne sont montées qu'après l'authentification
 * (pas de rendu serveur), donc la lecture du sessionStorage au rendu est sûre.
 */
export function useSearchId(projectId: string): string | undefined {
  const fromUrl = useSearchParams().get("search");
  useEffect(() => {
    if (fromUrl) storeSearchId(projectId, fromUrl);
  }, [projectId, fromUrl]);
  return resolveSearchId(fromUrl, readStored(projectId));
}

/** Recherches du projet (null tant que non chargées) + la courante ; `refreshKey` change → relecture. */
export function useSearches(projectId: string, searchId?: string, refreshKey?: string) {
  const [searches, setSearches] = useState<SearchInfo[] | null>(null);
  const reload = useCallback(
    () =>
      listSearches(projectId)
        .then(setSearches)
        .catch(() => setSearches((s) => s ?? [])), // liste indisponible : les pages ne restent pas en attente
    [projectId]
  );
  useEffect(() => {
    void reload();
  }, [reload, refreshKey]);
  // Une autre partie de l'écran a créé / renommé / supprimé une recherche.
  useEffect(() => {
    window.addEventListener(SEARCHES_EVENT, reload);
    return () => window.removeEventListener(SEARCHES_EVENT, reload);
  }, [reload]);
  return { searches, current: currentSearch(searches, searchId), reload };
}

/**
 * Change de recherche courante : mémorise le choix et réécrit l'URL courante avec `?search=`
 * (l'article ouvert n'est pas forcément dans le lot de la nouvelle recherche : on l'oublie).
 */
export function useSelectSearch(projectId: string): (searchId: string) => void {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  return useCallback(
    (searchId: string) => {
      storeSearchId(projectId, searchId);
      const params = new URLSearchParams(sp.toString());
      params.set("search", searchId);
      params.delete("article");
      router.replace(`${pathname}?${params.toString()}`);
    },
    [projectId, router, pathname, sp]
  );
}
