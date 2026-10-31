/** Display version of a model: Ollama digest (64 hex) → first 12 (the ID column of
 * `ollama list`); long version → truncated to 40 characters; empty → null. */
export function shortVersion(v?: string | null): string | null {
  if (!v) return null;
  if (/^(sha256:)?[0-9a-f]{64}$/i.test(v)) return v.replace(/^sha256:/i, "").slice(0, 12);
  const chars = Array.from(v); // code points: no surrogate pair cut in half
  return chars.length > 40 ? `${chars.slice(0, 39).join("")}…` : v;
}
