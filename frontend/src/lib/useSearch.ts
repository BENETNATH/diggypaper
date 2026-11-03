"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
  const [listFailed, setListFailed] = useState(false);
  // Number of list fetches in flight (ref, not state: the stale check below reads it in the
  // same effect pass, where a state update would not be visible yet).
  const inFlight = useRef(0);
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const reload = useCallback(() => {
    inFlight.current += 1;
    listSearches(projectId)
      .then(
        (items) => {
          setSearches(items);
          setListFailed(false);
        },
        () => {
          setListFailed(true);
          setSearches((s) => s ?? []); // list unavailable: the pages do not stay pending
        },
      )
      .finally(() => {
        inFlight.current -= 1;
      });
  }, [projectId]);
  useEffect(() => {
    void reload();
  }, [reload, refreshKey]);
  // Another part of the screen created / renamed / deleted a search.
  useEffect(() => {
    window.addEventListener(SEARCHES_EVENT, reload);
    return () => window.removeEventListener(SEARCHES_EVENT, reload);
  }, [reload]);

  // The selection points at a search that no longer exists (it was deleted): fall back to
  // the project's default search by clearing the stale reference (tab memory + `?search=`
  // in the URL). Without this, the per-search endpoints 404 and the pages show a spurious
  // "not found" banner. Guards: a just-created search may not be in the list being
  // fetched yet (inFlight), and a failed fetch must not look like an empty list (listFailed).
  useEffect(() => {
    const stale =
      !!searchId &&
      !listFailed &&
      inFlight.current === 0 &&
      searches !== null &&
      !searches.some((s) => s.id === searchId);
    if (!stale) return;
    try {
      sessionStorage.removeItem(searchStoreKey(projectId));
    } catch {
      /* storage unavailable: the selection survives only through the URL */
    }
    const params = new URLSearchParams(sp.toString());
    if (params.get("search")) {
      params.delete("search");
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname);
    }
  }, [searchId, listFailed, searches, projectId, sp, pathname, router]);

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
