import { sourceLabel } from "@/lib/source";
import en from "@/messages/en.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import es from "@/messages/es.json";

/** Source of each article (spec 2026-10-29-sources-phase2-identite §5). */

describe("sourceLabel", () => {
  it("PubMed : « PubMed PMID n »", () => {
    expect(sourceLabel("pubmed", "123", "fichier importé")).toBe("PubMed PMID 123");
  });

  it("Crossref : « Crossref DOI … »", () => {
    expect(sourceLabel("crossref", "10.1000/x", "fichier importé")).toBe("Crossref DOI 10.1000/x");
  });

  it("file: the translated label provided, no identifier", () => {
    expect(sourceLabel("file", null, "imported file")).toBe("imported file");
  });

  it("Europe PMC (phase 3): proper name, in all 4 languages + identifier", () => {
    expect(sourceLabel("europepmc", "W123", "x")).toBe("Europe PMC W123");
  });

  it("OpenAlex (phase 4a) : nom propre + identifiant", () => {
    expect(sourceLabel("openalex", "W190488077", "x")).toBe("OpenAlex W190488077");
  });

  it("source inconnue (phases suivantes) : code brut + identifiant", () => {
    expect(sourceLabel("medline", "t-0000", "x")).toBe("medline t-0000");
    expect(sourceLabel("medline", null, "x")).toBe("medline");
  });

  it("no source (old data) → null: the caller shows its fallback", () => {
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
  it.each(LOCALES)("keys present and non-empty (%s)", (_, messages) => {
    for (const key of ["sourceFile", "sourceNotRecorded"]) {
      expect(typeof messages.articles?.[key]).toBe("string");
      expect(messages.articles[key].trim().length).toBeGreaterThan(0);
    }
  });

  it('searchPlaceholder: the generic identifier, no bare "PMID" (phase 3)', () => {
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

  it("actually translated", () => {
    const base = (en as unknown as Messages).articles;
    for (const [lang, messages] of LOCALES) {
      if (lang === "en") continue;
      expect(messages.articles.sourceFile).not.toBe(base.sourceFile);
      expect(messages.articles.sourceNotRecorded).not.toBe(base.sourceNotRecorded);
    }
  });
});

/** Phase 4a: source selector + OpenAlex — the new strings exist in the
 * 4 languages (user constraint: everything is multilingual). */
describe("i18n phase 4a", () => {
  it.each(LOCALES)("import/annotate/settings keys present and non-empty (%s)", (_, m) => {
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

  it.each(LOCALES)('generic queryLabel: no hardcoded "PubMed" (%s)', (_, m) => {
    expect(m.import.queryLabel).not.toContain("PubMed");
  });

  it.each(LOCALES)(
    "intro + howStep1 cite the 3 selectable sources (%s)",
    (_, m) => {
      for (const text of [m.import.intro, m.project.howStep1]) {
        expect(text).toContain("PubMed");
        expect(text).toContain("Europe PMC");
        expect(text).toContain("OpenAlex");
      }
    },
  );

  it.each(LOCALES)(
    'actually translated (phase 4a; "Source" is identical in fr/en) (%s)',
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
