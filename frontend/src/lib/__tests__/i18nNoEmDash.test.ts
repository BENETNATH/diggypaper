import * as fs from "node:fs";
import * as path from "node:path";

// audit antislop 2026-11-23 F-1: the em-dash (U+2014) was replaced by the
// en-dash (U+2013) everywhere in user-facing copy (owner decision 2026-11-23).
// Regression guard: no locale file may contain an em-dash again.
const messagesDir = path.join(__dirname, "..", "..", "messages");

describe("i18n copy (audit 2026-11-23 F-1)", () => {
  for (const locale of ["en", "fr", "de", "es"]) {
    it(`${locale}: no em-dash (U+2014) in user-facing copy`, () => {
      const raw = fs.readFileSync(path.join(messagesDir, `${locale}.json`), "utf8");
      expect(raw).not.toContain("\u2014");
    });
  }
});
