/** Libellé lisible de la source d'un article (mêmes règles que `app.sources.source_label`) :
 * « PubMed PMID 123 », « Crossref DOI 10.1/x », le libellé traduit pour un fichier ; `null` sans
 * source (donnée ancienne : l'appelant affiche son repli). */
const NAMES: Record<string, string> = {
  pubmed: "PubMed",
  europepmc: "Europe PMC",
  openalex: "OpenAlex",
  hal: "HAL",
  crossref: "Crossref",
};
const ID_KINDS: Record<string, string> = { pubmed: "PMID", crossref: "DOI" };

/** Nom lisible d'une source (« europepmc » → « Europe PMC ») ; `null` si code inconnu. */
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
