import { sourceLabel } from "@/lib/source";
import en from "@/messages/en.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import es from "@/messages/es.json";

/** Source de chaque article (spec 2026-10-29-sources-phase2-identite §5). */

describe("sourceLabel", () => {
  it("PubMed : « PubMed PMID n »", () => {
    expect(sourceLabel("pubmed", "123", "fichier importé")).toBe("PubMed PMID 123");
  });

  it("Crossref : « Crossref DOI … »", () => {
    expect(sourceLabel("crossref", "10.1000/x", "fichier importé")).toBe("Crossref DOI 10.1000/x");
  });

  it("fichier : le libellé traduit fourni, sans identifiant", () => {
    expect(sourceLabel("file", null, "imported file")).toBe("imported file");
  });

  it("source inconnue (phases suivantes) : code brut + identifiant", () => {
    expect(sourceLabel("europepmc", "PMC9", "x")).toBe("europepmc PMC9");
    expect(sourceLabel("openalex", null, "x")).toBe("openalex");
  });

  it("sans source (donnée ancienne) → null : l'appelant affiche son repli", () => {
    expect(sourceLabel(null, "123", "x")).toBeNull();
    expect(sourceLabel(undefined, undefined, "x")).toBeNull();
  });
});

type Messages = Record<string, Record<string, string>>;
const LOCALES: [string, Messages][] = [
  ["en", en as unknown as Messages],
  ["fr", fr as unknown as Messages],
  ["de", de as unknown as Messages],
  ["es", es as unknown as Messages],
];

describe("i18n source", () => {
  it.each(LOCALES)("clés présentes et non vides (%s)", (_, messages) => {
    for (const key of ["sourceFile", "sourceNotRecorded"]) {
      expect(typeof messages.articles?.[key]).toBe("string");
      expect(messages.articles[key].trim().length).toBeGreaterThan(0);
    }
  });

  it("réellement traduites", () => {
    const base = (en as unknown as Messages).articles;
    for (const [lang, messages] of LOCALES) {
      if (lang === "en") continue;
      expect(messages.articles.sourceFile).not.toBe(base.sourceFile);
      expect(messages.articles.sourceNotRecorded).not.toBe(base.sourceNotRecorded);
    }
  });
});
