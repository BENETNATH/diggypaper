import { modelErrorMessage } from "@/lib/modelError";
import en from "@/messages/en.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import es from "@/messages/es.json";

type Messages = { models: Record<string, string> };

/** Minimal next-intl-style t() (namespace "models"): missing key → exception (i18n parity). */
function tFor(messages: Messages) {
  return (key: string, values?: Record<string, string | number>) => {
    const raw = messages.models[key];
    if (typeof raw !== "string") throw new Error(`missing key: models.${key}`);
    return raw.replace(/\{(\w+)\}/g, (_, k: string) => String(values?.[k] ?? `{${k}}`));
  };
}

/** Error codes returned by the backend on the /models page (spec 2026-10-29 §8). */
const CODES = [
  "timeout",
  "connection_error",
  "url_not_allowed",
  "invalid_json",
  "invalid_response",
  "ollama_disabled_by_deployment",
  "ollama_base_url_is_compose_default",
  "ollama_unreachable",
  "ollama_delete_failed",
  "model_in_use",
  "model_busy",
];

const LOCALES: [string, Messages][] = [
  ["en", en as unknown as Messages],
  ["fr", fr as unknown as Messages],
  ["de", de as unknown as Messages],
  ["es", es as unknown as Messages],
];

describe("modelErrorMessage", () => {
  it.each(LOCALES)("traduit chaque code connu (%s)", (_, messages) => {
    const t = tFor(messages);
    for (const code of CODES) {
      const msg = modelErrorMessage(code, t);
      expect(msg).not.toBe(code);
      expect(msg.trim().length).toBeGreaterThan(0);
    }
  });

  it.each(LOCALES)("« HTTP nnn » : message traduit qui garde le statut (%s)", (_, messages) => {
    const msg = modelErrorMessage("HTTP 404", tFor(messages));
    expect(msg).not.toBe("HTTP 404");
    expect(msg).toContain("404");
  });

  it("code inconnu : repli sur le code brut", () => {
    expect(modelErrorMessage("pull already running", tFor(fr as unknown as Messages))).toBe(
      "pull already running"
    );
  });
});
