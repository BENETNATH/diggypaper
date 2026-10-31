/** Backend error codes of the /models page (inference, Ollama deployment mode, deletion). */
const KNOWN = new Set([
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
]);

/** Translated message of an error code (namespace "models"); unknown code → raw code. */
export function modelErrorMessage(
  code: string,
  t: (key: string, values?: Record<string, string | number>) => string
): string {
  const http = /^HTTP (\d{3})$/.exec(code);
  if (http) return t("err_http", { status: http[1] });
  return KNOWN.has(code) ? t(`err_${code}`) : code;
}
