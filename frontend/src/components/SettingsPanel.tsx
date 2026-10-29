"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { getSettings, getEnvSettings, updateSettings, testSmtp, listOllama, EnvSettings, SettingsScope } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Card } from "@/components/ui/Card";
import { Skeleton, ErrorBanner } from "@/components/ui/Feedback";

const MASK = "••••••••";

interface FieldDef {
  key: string;
  labelKey: string;
  hintKey?: string;
  secret?: boolean;
  type?: "text" | "number" | "email" | "boolean";
}

const GROUPS: { titleKey: string; helpKey?: string; fields: FieldDef[] }[] = [
  {
    titleKey: "llm",
    helpKey: "llmHelp",
    fields: [
      { key: "llm_api_base_url", labelKey: "llmBaseUrl", hintKey: "llmBaseUrlHint", type: "text" },
      { key: "llm_api_key", labelKey: "llmApiKey", hintKey: "llmApiKeyHint", secret: true, type: "text" },
    ],
  },
  {
    titleKey: "smtp",
    helpKey: "smtpHelp",
    fields: [
      { key: "smtp_host", labelKey: "smtpHost", type: "text" },
      { key: "smtp_port", labelKey: "smtpPort", type: "number" },
      { key: "smtp_user", labelKey: "smtpUser", type: "text" },
      { key: "smtp_password", labelKey: "smtpPassword", secret: true, type: "text" },
      { key: "smtp_from_email", labelKey: "smtpFrom", type: "email" },
      { key: "use_tls", labelKey: "useTls", type: "boolean" },
    ],
  },
  {
    titleKey: "ncbi",
    helpKey: "ncbiHelp",
    fields: [{ key: "ncbi_api_key", labelKey: "ncbiKey", secret: true, type: "text" }],
  },
  {
    titleKey: "openalex",
    helpKey: "openalexHelp",
    fields: [{ key: "openalex_api_key", labelKey: "openalexKey", secret: true, type: "text" }],
  },
  {
    titleKey: "contact",
    helpKey: "contactHelp",
    fields: [{ key: "contact_email", labelKey: "contactEmail", type: "text" }],
  },
  {
    titleKey: "zotero",
    helpKey: "zoteroHelp",
    fields: [
      { key: "zotero_group_id", labelKey: "zoteroGroup", type: "text" },
      { key: "zotero_api_key", labelKey: "zoteroKey", secret: true, type: "text" },
    ],
  },
];

/**
 * Panneau de settings (Module 7). Réutilisé par /settings (global, admin) et
 * /projects/[id]/settings (scope GLOBAL + PROJECT).
 *
 * - Sans `projectId` : scope GLOBAL uniquement (config serveur : LLM/SMTP/NCBI/Zotero).
 * - Avec `projectId` : scope GLOBAL + PROJECT (le PROJECT passe project_id à l'API).
 */
export function SettingsPanel({ projectId }: { projectId?: string }) {
  const t = useTranslations("settings");
  const scopes: SettingsScope[] = projectId ? ["GLOBAL", "PROJECT"] : ["GLOBAL"];
  const [scope, setScope] = useState<SettingsScope>("GLOBAL");
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [smtpMsg, setSmtpMsg] = useState<string | null>(null);
  const [smtpTesting, setSmtpTesting] = useState(false);
  const [llmMsg, setLlmMsg] = useState<string | null>(null);
  const [env, setEnv] = useState<EnvSettings>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getSettings({
        scope,
        project_id: scope === "PROJECT" ? projectId : undefined,
      });
      const next: Record<string, string> = {};
      for (const g of GROUPS) {
        for (const f of g.fields) {
          const raw = data[scope]?.[f.key] ?? null;
          next[f.key] = raw === null ? "" : raw;
        }
      }
      setValues(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    } finally {
      setLoading(false);
    }
  }, [scope, projectId]);

  useEffect(() => {
    void load();
  }, [load]);
  // Valeurs de l'environnement (admin, scope global) : montrent ce qui s'applique quand un champ est vide.
  useEffect(() => {
    getEnvSettings().then(setEnv).catch(() => setEnv({}));
  }, []);

  function setField(key: string, v: string) {
    setValues((prev) => ({ ...prev, [key]: v }));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await updateSettings({
        scope,
        project_id: scope === "PROJECT" ? projectId : undefined,
        values,
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleSmtpTest() {
    setSmtpTesting(true);
    setSmtpMsg(null);
    try {
      const res = await testSmtp();
      setSmtpMsg(res.sent ? t("smtpOk") : res.error ? t("smtpFail") : t("smtpOk"));
    } catch (err) {
      setSmtpMsg(err instanceof Error ? err.message : t("smtpFail"));
    } finally {
      setSmtpTesting(false);
    }
  }

  async function handleLlmTest() {
    setLlmMsg(null);
    try {
      const r = await listOllama();
      setLlmMsg(`✓ ${t("llmOk")} (${r.items.length})`);
    } catch {
      setLlmMsg(`⚠ ${t("llmFail")}`);
    }
  }

  if (loading) return <Skeleton rows={5} />;
  if (error && Object.keys(values).length === 0)
    return <ErrorBanner message={error} onRetry={() => void load()} />;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        {scopes.length > 1 && (
          <Field label={t("scope")}>
            <Select value={scope} onChange={(e) => setScope(e.target.value as SettingsScope)}>
              <option value="GLOBAL">{t("scopeGlobal")}</option>
              <option value="PROJECT">{t("scopeProject")}</option>
            </Select>
          </Field>
        )}
      </div>

      {error && <ErrorBanner message={error} />}

      <form onSubmit={handleSave} className="flex flex-col gap-4">
        {GROUPS.map((group) => (
          <Card key={group.titleKey} title={t(group.titleKey)}>
            {group.helpKey && <p className="mb-3 text-sm text-muted">{t(group.helpKey)}</p>}
            {scope === "GLOBAL" && group.fields.some((f) => env[f.key]?.set && !values[f.key]) && (
              <p className="mb-3 rounded-md bg-primary/5 px-3 py-2 text-xs text-foreground">{t("envNote")}</p>
            )}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {group.fields.map((f) => (
                <Field key={f.key} label={t(f.labelKey)} hint={f.hintKey ? t(f.hintKey) : undefined}>
                  {f.type === "boolean" ? (
                    <Select
                      value={values[f.key] || ""}
                      onChange={(e) => setField(f.key, e.target.value)}
                    >
                      <option value="">
                        {scope === "GLOBAL" && env[f.key]?.set
                          ? t("fromEnvValue", { value: env[f.key].value === "true" ? t("yes") : t("no") })
                          : t("unset")}
                      </option>
                      <option value="true">{t("yes")}</option>
                      <option value="false">{t("no")}</option>
                    </Select>
                  ) : (
                    <Input
                      type={f.secret ? "password" : f.type ?? "text"}
                      value={values[f.key] ?? ""}
                      onChange={(e) => setField(f.key, e.target.value)}
                      placeholder={
                        f.secret && values[f.key] === MASK
                          ? MASK
                          : scope === "GLOBAL" && !values[f.key] && env[f.key]?.set
                            ? f.secret
                              ? t("fromEnvSecret")
                              : (env[f.key].value ?? undefined)
                            : undefined
                      }
                    />
                  )}
                </Field>
              ))}
            </div>
            {group.titleKey === "llm" && (
              <div className="mt-3 flex items-center gap-3">
                <Button type="button" variant="secondary" size="sm" onClick={() => void handleLlmTest()}>
                  {t("llmTest")}
                </Button>
                {llmMsg && <span className="text-sm text-muted">{llmMsg}</span>}
              </div>
            )}
          </Card>
        ))}

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" loading={saving}>
            {t("save")}
          </Button>
          <Button type="button" variant="secondary" loading={smtpTesting} onClick={handleSmtpTest}>
            {t("testSmtp")}
          </Button>
          {smtpMsg && <span className="text-sm text-muted">{smtpMsg}</span>}
        </div>
      </form>
    </div>
  );
}
