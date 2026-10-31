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

  useEffect(() => {
    if (!active) return;
    stopped.current = false;

    async function tick() {
      if (stopped.current) return;
      try {
        const result = await fn();
        setData(result);
        setError(null);
        if (stopWhen(result)) {
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, intervalMs]);

  return { data, error, loading };
}
