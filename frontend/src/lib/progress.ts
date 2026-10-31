/** Speed and remaining time of a task, estimated on a sliding window of samples. */

export type Sample = { t: number; done: number };

/** Adds a sample (t in ms); keeps the last `keep`; resets if `done` goes back. */
export function pushSample(samples: Sample[], now: number, done: number, keep = 12): Sample[] {
  const last = samples[samples.length - 1];
  if (last && done < last.done) return [{ t: now, done }];
  if (last && done === last.done && now - last.t < 1000) return samples;
  return [...samples, { t: now, done }].slice(-keep);
}

/** Items processed per second, or null until the estimate is reliable. */
export function ratePerSecond(samples: Sample[]): number | null {
  if (samples.length < 2) return null;
  const a = samples[0];
  const b = samples[samples.length - 1];
  const dt = (b.t - a.t) / 1000;
  const dn = b.done - a.done;
  if (dt < 5 || dn <= 0) return null;
  return dn / dt;
}

/** Remaining seconds, or null if the speed is unknown. */
export function etaSeconds(done: number, total: number, rate: number | null): number | null {
  if (rate === null || rate <= 0 || total <= done) return null;
  return (total - done) / rate;
}

/** "45 s", "14 min", "1 h 05" (identical units in fr/en/de/es). */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s} s`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${String(m % 60).padStart(2, "0")}`;
}
