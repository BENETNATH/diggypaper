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

  it("Europe PMC (phase 3) : nom propre, même dans les 4 langues + identifiant", () => {
    expect(sourceLabel("europepmc", "W123", "x")).toBe("Europe PMC W123");
  });

  it("OpenAlex (phase 4a) : nom propre + identifiant", () => {
    expect(sourceLabel("openalex", "W190488077", "x")).toBe("OpenAlex W190488077");
  });

  it("source inconnue (phases suivantes) : code brut + identifiant", () => {
    expect(sourceLabel("hal", "t-0000", "x")).toBe("hal t-0000");
    expect(sourceLabel("hal", null, "x")).toBe("hal");
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

  it("searchPlaceholder : l'identifiant générique, plus « PMID » seule (phase 3)", () => {
    const words: Record<string, string> = {
      en: "identifier",
      fr: "identifiant",
      de: "Identifikator",
      es: "identificador",
    };
    for (const [lang, messages] of LOCALES) {
      expect(messages.articles.searchPlaceholder).toContain(words[lang]);
      expect(messages.articles.searchPlaceholder).not.toContain("PMID");
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

/** Phase 4a : sélecteur de source + OpenAlex — les chaînes nouvelles existent dans les
 * 4 langues (contrainte utilisateur : tout est multilingue). */
describe("i18n phase 4a", () => {
  it.each(LOCALES)("clés import/annotate/settings présentes et non vides (%s)", (_, m) => {
    for (const [ns, key] of [
      ["import", "sourceLabel"],
      ["import", "queryHintGeneric"],
      ["annotate", "metadataOnlyNote"],
      ["settings", "openalex"],
      ["settings", "openalexKey"],
      ["settings", "openalexHelp"],
    ] as const) {
      expect(typeof m[ns]?.[key]).toBe("string");
      expect((m[ns]?.[key] ?? "").trim().length).toBeGreaterThan(0);
    }
  });

  it.each(LOCALES)("queryLabel générique : plus « PubMed » en dur (%s)", (_, m) => {
    expect(m.import.queryLabel).not.toContain("PubMed");
  });

  it.each(LOCALES)(
    "intro + howBody citent les 3 sources sélectionnables (%s)",
    (_, m) => {
      for (const text of [m.import.intro, m.project.howBody]) {
        expect(text).toContain("PubMed");
        expect(text).toContain("Europe PMC");
        expect(text).toContain("OpenAlex");
      }
    },
  );

  it.each(LOCALES)(
    "réellement traduites (phase 4a ; « Source » est identique en fr/en) (%s)",
    (_, m) => {
      const base = en as unknown as Messages;
      if (m === en) return;
      for (const [ns, key] of [
        ["import", "queryHintGeneric"],
        ["annotate", "metadataOnlyNote"],
        ["settings", "openalexHelp"],
      ] as const) {
        expect(m[ns][key]).not.toBe(base[ns][key]);
      }
    },
  );
});
