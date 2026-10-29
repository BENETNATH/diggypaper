/** Version d'un modèle pour l'affichage : digest Ollama (64 hex) → 12 premiers (colonne ID de
 * `ollama list`) ; version longue → tronquée à 40 caractères ; vide → null. */
export function shortVersion(v?: string | null): string | null {
  if (!v) return null;
  if (/^(sha256:)?[0-9a-f]{64}$/i.test(v)) return v.replace(/^sha256:/i, "").slice(0, 12);
  const chars = Array.from(v); // points de code : pas de paire de substitution coupée en deux
  return chars.length > 40 ? `${chars.slice(0, 39).join("")}…` : v;
}
