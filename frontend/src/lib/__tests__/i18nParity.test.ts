import en from "@/messages/en.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import es from "@/messages/es.json";

/** Flattens a locale file into its dot-notation key list. */
const flatten = (m: Record<string, unknown>, prefix = ""): string[] =>
  Object.entries(m).flatMap(([k, v]) => (typeof v === "string" ? [`${prefix}${k}`] : flatten(v as Record<string, unknown>, `${prefix}${k}.`)));

// spec 2026-11-02-frontend-vocabulary-a11y-polish: written BEFORE the §1/§2 rewrites,
// so any key forgotten in one locale (or added in only some) fails here.
describe("i18n key parity (4 locales)", () => {
  it("fr, en, de, es have exactly the same key set", () => {
    const sets = {
      fr: new Set(flatten(fr as Record<string, unknown>)),
      en: new Set(flatten(en as Record<string, unknown>)),
      de: new Set(flatten(de as Record<string, unknown>)),
      es: new Set(flatten(es as Record<string, unknown>)),
    };
    for (const [loc, keys] of Object.entries(sets).filter(([loc]) => loc !== "fr")) {
      const missing = [...sets.fr].filter((k) => !keys.has(k)).sort();
      const extra = [...keys].filter((k) => !sets.fr.has(k)).sort();
      expect({ [loc]: { missing, extra } }).toEqual({ [loc]: { missing: [], extra: [] } });
    }
  });

  // spec 2026-11-09-clarte-1: copy pins — removed keys gone everywhere, added keys present everywhere
  it("spec clarte-1: searchLabel/allSearches/activeTag absent, kpiScopeLine present, in the 4 locales", () => {
    const all = [fr, en, de, es];
    for (const m of all) {
      for (const gone of ["searchLabel", "allSearches", "activeTag"]) {
        expect((m.project as Record<string, unknown>)[gone]).toBeUndefined();
      }
      expect((m.project as Record<string, unknown>).kpiScopeLine).toBeTruthy();
    }
  });
});
