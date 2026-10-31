"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { listSearches, SearchInfo } from "@/lib/api";
import { currentSearch, resolveSearchId, searchStoreKey } from "@/lib/search";

const SEARCHES_EVENT = "diggy:searches";
/** Tells the mounted `useSearches` (layout selector) that the search list changed. */
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
    /* storage unavailable: the selection survives only through the URL */
  }
}

/**
 * Current search of the project: `?search=` in the URL, otherwise tab memory, otherwise
 * `undefined` (= initial search). Client pages mount only after authentication
 * (no server rendering), so reading sessionStorage at render is safe.
 */
export function useSearchId(projectId: string): string | undefined {
  const fromUrl = useSearchParams().get("search");
  useEffect(() => {
    if (fromUrl) storeSearchId(projectId, fromUrl);
  }, [projectId, fromUrl]);
  return resolveSearchId(fromUrl, readStored(projectId));
}

/** Project searches (null until loaded) + the current one; `refreshKey` changes → re-read. */
export function useSearches(projectId: string, searchId?: string, refreshKey?: string) {
  const [searches, setSearches] = useState<SearchInfo[] | null>(null);
  const reload = useCallback(
    () =>
      listSearches(projectId)
        .then(setSearches)
        .catch(() => setSearches((s) => s ?? [])), // list unavailable: the pages do not stay pending
    [projectId]
  );
  useEffect(() => {
    void reload();
  }, [reload, refreshKey]);
  // Another part of the screen created / renamed / deleted a search.
  useEffect(() => {
    window.addEventListener(SEARCHES_EVENT, reload);
    return () => window.removeEventListener(SEARCHES_EVENT, reload);
  }, [reload]);
  return { searches, current: currentSearch(searches, searchId), reload };
}

/**
 * Changes the current search: remembers the choice and rewrites the current URL with `?search=`
 * (the opened article may not be in the new search's batch: we drop it).
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
