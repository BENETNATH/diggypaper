/** Readable label of an article's source (same rules as `app.sources.source_label`):
 * "PubMed PMID 123", "Crossref DOI 10.1/x", the translated label for a file; `null` without
 * a source (older data: the caller shows its fallback). */
const NAMES: Record<string, string> = {
  pubmed: "PubMed",
  europepmc: "Europe PMC",
  openalex: "OpenAlex",
  hal: "HAL",
  crossref: "Crossref",
};
const ID_KINDS: Record<string, string> = { pubmed: "PMID", crossref: "DOI" };

/** Readable name of a source ("europepmc" → "Europe PMC"); `null` if the code is unknown. */
export const sourceName = (code: string): string | null => NAMES[code] ?? null;

export function sourceLabel(
  source: string | null | undefined,
  nativeId: string | null | undefined,
  fileLabel: string,
): string | null {
  if (!source) return null;
  const name = source === "file" ? fileLabel : (NAMES[source] ?? source);
  if (!nativeId) return name;
  const kind = ID_KINDS[source];
  return kind ? `${name} ${kind} ${nativeId}` : `${name} ${nativeId}`;
}
