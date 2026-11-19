"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Polling hook: calls `fn` every `intervalMs` ms until
 * `stopWhen(result)` is true (or `active` becomes false). Used for the
 * tracking of jobs (import / benchmark / inference).
 */
export function usePolling<T>(
  fn: () => Promise<T>,
  intervalMs: number,
  stopWhen: (result: T) => boolean,
  active = true
): { data: T | null; error: string | null; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(active);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stopped = useRef(false);
  // audit 2026-11-10 P3-9: the latest fn/stopWhen live in refs so a re-created closure is
  // picked up by the running interval without restarting the poll (stale-closure fix).
  // Updated in an effect — ref writes during render are forbidden (react-hooks/refs);
  // the first tick is scheduled after commit, so the refs are always fresh when read.
  const fnRef = useRef(fn);
  const stopWhenRef = useRef(stopWhen);
  useEffect(() => {
    fnRef.current = fn;
    stopWhenRef.current = stopWhen;
  }, [fn, stopWhen]);

  useEffect(() => {
    if (!active) return;
    stopped.current = false;

    async function tick() {
      if (stopped.current) return;
      try {
        const result = await fnRef.current();
        setData(result);
        setError(null);
        if (stopWhenRef.current(result)) {
          stopped.current = true;
          setLoading(false);
          return;
        }
      } catch (e) {
        setError((e as Error).message);
        setLoading(false);
        return;
      }
      timer.current = setTimeout(tick, intervalMs);
    }

    void tick();
    return () => {
      stopped.current = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [active, intervalMs]);

  return { data, error, loading };
}
