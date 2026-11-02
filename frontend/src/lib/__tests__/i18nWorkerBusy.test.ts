import fs from "node:fs";
import path from "node:path";

// i18n check: ``inference.workersBusy`` must stay free of infrastructure jargon
// ("worker") in all 4 locales while keeping the user-facing information.
const LOCALES = ["fr", "en", "de", "es"] as const;

const EXPECTED: Record<(typeof LOCALES)[number], string> = {
  fr: "Une analyse est déjà en cours dans « {name} ».",
  en: "An analysis is already running in “{name}”.",
  de: "In „{name}“ läuft bereits eine Analyse.",
  es: "Ya se está ejecutando un análisis en «{name}»",
};

describe("inference.workersBusy", () => {
  it.each(LOCALES.map((l) => [l] as const))("locale %s: no \"worker\" jargon, expected wording", (locale) => {
    const messages = JSON.parse(
      fs.readFileSync(path.join(__dirname, "../../messages", `${locale}.json`), "utf8")
    );
    const value: string = messages.inference.workersBusy;
    expect(value).toBe(EXPECTED[locale]);
    expect(value.toLowerCase()).not.toContain("worker");
    expect(value).toContain("{name}");
  });
});
