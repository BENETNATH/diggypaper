"use client";

import { useCallback, useEffect, useState } from "react";
import { getVectorizeProgress, triggerVectorize, VectorizeProgress } from "@/lib/api";

const POLL_MS = 2500;

/**
 * Live status of the corpus index + a trigger (single source of truth).
 *
 * One cheap read on mount, guarded by project (a switch never shows stale data
 * — no reset setState in the effect). While a run is in flight
 * (``status === "RUNNING"``) or right after a trigger (``wantRun`` until the run
 * reaches DONE/ERROR) it re-reads every couple of seconds. This replaces the
 * previous probe (a fake semantic search that loaded every section just to
 * learn the ``indexed`` flag).
 */
export function useVectorizeProgress(projectId: string | null) {
  const [state, setState] = useState<{ id: string; data: VectorizeProgress } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wantRun, setWantRun] = useState(false);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      let keep = false;
      try {
        const p = await getVectorizeProgress(projectId);
        if (cancelled) return;
        setState({ id: projectId, data: p });
        setError(null);
        if (wantRun && (p.status === "DONE" || p.status === "ERROR")) {
          setWantRun(false); // the run we triggered is over → stop following it
          return;
        }
        keep = p.status === "RUNNING" || wantRun;
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "error");
        keep = wantRun;
      }
      if (!cancelled && keep) timer = setTimeout(tick, POLL_MS);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [projectId, wantRun]);

  const index = useCallback(async () => {
    if (!projectId) return;
    setWantRun(true);
    setError(null);
    try {
      await triggerVectorize(projectId);
    } catch (e) {
      setWantRun(false);
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId]);

  // Guarded by project: a switch never shows the previous project's state.
  const progress = state && state.id === projectId ? state.data : null;
  return { progress, error, indexing: wantRun, index };
}
