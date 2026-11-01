import { shortVersion } from "@/lib/modelVersion";
import en from "@/messages/en.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import es from "@/messages/es.json";

/** Model + version traceability (spec 2026-10-29-tracabilite-modele-version §6). */

const DIGEST = "a1b2c3d4e5f6" + "0".repeat(52); // digest Ollama (/api/tags) : 64 hex

describe("shortVersion", () => {
  it('absent → null (the UI shows "version not recorded")', () => {
    expect(shortVersion(null)).toBeNull();
    expect(shortVersion(undefined)).toBeNull();
    expect(shortVersion("")).toBeNull();
  });

  it("digest Ollama → 12 premiers hex (colonne ID de `ollama list`)", () => {
    expect(shortVersion(DIGEST)).toBe("a1b2c3d4e5f6");
    expect(shortVersion("sha256:" + DIGEST)).toBe("a1b2c3d4e5f6");
    expect(shortVersion(DIGEST.toUpperCase())).toBe("A1B2C3D4E5F6");
    expect(shortVersion("SHA256:" + DIGEST)).toBe("a1b2c3d4e5f6"); // case-insensitive prefix
    expect(shortVersion("Sha256:" + DIGEST.toUpperCase())).toBe("A1B2C3D4E5F6");
  });

  it("version servie (API externe, llama.cpp) : telle quelle si courte", () => {
    expect(shortVersion("gpt-4o-2024-08-06")).toBe("gpt-4o-2024-08-06");
    expect(shortVersion("a1b2c3")).toBe("a1b2c3"); // not a full digest: unchanged
  });

  it('long (over 40 characters) → 39 characters + "…"', () => {
    const long = "models/" + "x".repeat(60) + ".gguf";
    const out = shortVersion(long) as string;
    expect(out).toHaveLength(40);
    expect(out.endsWith("…")).toBe(true);
    expect(long.startsWith(out.slice(0, 39))).toBe(true);
  });

  it("safe truncation for surrogate pairs (no half emoji)", () => {
    const out = shortVersion("😀".repeat(50)) as string;
    expect(Array.from(out)).toHaveLength(40);
    expect(out).toBe("😀".repeat(39) + "…");
  });
});

type Messages = Record<string, Record<string, string>>;
const LOCALES: [string, Messages][] = [
  ["en", en as unknown as Messages],
  ["fr", fr as unknown as Messages],
  ["de", de as unknown as Messages],
  ["es", es as unknown as Messages],
];
const KEYS: [string, string][] = [
  ["common", "modelVersion"],
  ["common", "versionNotRecorded"],
  ["inference", "prefilterShort"],
];

describe("i18n traceability", () => {
  it.each(LOCALES)("keys present and non-empty (%s)", (_, messages) => {
    for (const [ns, key] of KEYS) {
      expect(typeof messages[ns]?.[key]).toBe("string");
      expect(messages[ns][key].trim().length).toBeGreaterThan(0);
    }
  });

  it('"version not recorded" actually translated', () => {
    const enText = (en as unknown as Messages).common.versionNotRecorded;
    for (const [lang, messages] of LOCALES) {
      if (lang !== "en") expect(messages.common.versionNotRecorded).not.toBe(enText);
    }
  });
});
