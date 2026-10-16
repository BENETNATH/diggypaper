"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import {
  listModels,
  deleteModel,
  listOllama,
  pullOllama,
  getModelCapabilities,
  ModelCapabilities,
  registerOllama,
  testExternalModel,
  addExternalModel,
  ModelInfo,
  OllamaModel,
  PullState,
} from "@/lib/api";
import { RequireAuth } from "@/components/RequireAuth";
import { ModelSharing } from "@/components/ModelSharing";
import { useAuth } from "@/lib/auth";
import { HfSearch } from "@/components/HfSearch";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Card, Badge } from "@/components/ui/Card";
import { ErrorBanner, EmptyState, Skeleton, ProgressBar } from "@/components/ui/Feedback";

type Pull = PullState & { name?: string };

function ModelsContent() {
  const t = useTranslations("models");
  const isAdmin = useAuth().user?.role === "ADMIN";
  const [sharing, setSharing] = useState<string | null>(null);
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [detected, setDetected] = useState<OllamaModel[]>([]);
  const [pulls, setPulls] = useState<Record<string, Pull>>({});
  const [error, setError] = useState<string | null>(null);
  const [caps, setCaps] = useState<ModelCapabilities | null>(null);
  const [ext, setExt] = useState({ name: "", base_url: "", model: "", api_key: "" });
  const [extMsg, setExtMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setModels(await listModels());
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
    getModelCapabilities().then(setCaps).catch(() => undefined);
    if (!isAdmin) return;
    try {
      const o = await listOllama();
      setDetected(o.items.filter((m) => !m.registered));
      setPulls(o.pulls);
    } catch {
      /* Ollama injoignable : la liste des modèles installés reste utilisable */
    }
  }, [isAdmin]);

  useEffect(() => {
    void load();
  }, [load]);

  // Un seul panneau de suivi : on interroge tant qu'un téléchargement tourne.
  const running = Object.entries(pulls).filter(([, p]) => p.state === "running");
  const failed = Object.entries(pulls).filter(([, p]) => p.state === "error");
  useEffect(() => {
    if (running.length === 0) return;
    const id = setInterval(() => void load(), 2000);
    return () => clearInterval(id);
  }, [running.length, load]);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }

  async function testExt() {
    setExtMsg(null);
    try {
      const r = await testExternalModel(ext);
      setExtMsg({ ok: r.ok, text: r.ok ? t("extOk") : t("extFail", { error: r.error ?? "?" }) });
    } catch (e) {
      setExtMsg({ ok: false, text: e instanceof Error ? e.message : "error" });
    }
  }

  async function addExt(e: React.FormEvent) {
    e.preventDefault();
    await run(async () => {
      await addExternalModel(ext);
      setExt({ name: "", base_url: "", model: "", api_key: "" });
      setExtMsg(null);
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">{isAdmin ? t("intro") : t("introUser")}</p>
      </div>

      {error && <ErrorBanner message={error} onRetry={() => void load()} />}

      {(running.length > 0 || failed.length > 0) && (
        <Card title={t("downloads")}>
          <div className="flex flex-col gap-3">
            {running.map(([k, p]) => (
              <ProgressBar
                key={k}
                value={p.total ? (p.completed / p.total) * 100 : null}
                label={`${p.name ?? k} — ${p.status}`}
              />
            ))}
            {failed.map(([k, p]) => (
              <p key={k} className="text-sm text-danger">
                ⚠ {p.name ?? k} : {/MLX/i.test(p.error ?? "") ? t("errMlx") : p.error}
              </p>
            ))}
          </div>
        </Card>
      )}

      {caps && (
        <Card title={t("servicesTitle")}>
          <ul className="flex flex-col gap-2 text-sm">
            <li className="flex flex-wrap items-center gap-2">
              <Badge color={caps.ollama.reachable ? "green" : caps.ollama.configured ? "red" : "gray"}>
                {caps.ollama.reachable ? t("svcUp") : caps.ollama.configured ? t("svcDown") : t("svcOff")}
              </Badge>
              <span className="font-medium text-foreground">{t("svcOllama")}</span>
              {!caps.ollama.reachable && <span className="text-xs text-muted">{t("svcHow", { profile: "ollama" })}</span>}
            </li>
            <li className="flex flex-wrap items-center gap-2">
              <Badge color="green">{t("svcUp")}</Badge>
              <span className="font-medium text-foreground">{t("svcExternal")}</span>
            </li>
          </ul>
          <div className="mt-4 border-t border-border pt-3 text-sm">
            <p className="font-medium text-foreground">
              {t("hwTitle")} : {caps.hardware.cores} {t("hwCores")} · {caps.hardware.ram_gb} {t("hwRam")} ·{" "}
              {caps.hardware.gpu ? t("hwGpu") : t("hwNoGpu")}
              {caps.hardware.avx2 ? "" : ` · ${t("hwNoAvx2")}`}
            </p>
            {caps.recommendation.slow_cpu && <p className="mt-1 text-xs text-warning">{t("hwSlow")}</p>}
            <p className="mt-2 text-xs text-muted">{t(`hwProfile_${caps.recommendation.profile}`)}</p>
            <ul className="mt-2 flex flex-col gap-2">
              {caps.recommendation.models.map((m, i) => (
                <li key={m.tag} className="flex flex-wrap items-center gap-2">
                  <code className="rounded bg-surface-muted px-1.5 py-0.5 text-xs">{m.tag}</code>
                  <span className="text-xs text-muted">
                    ≈ {m.ram_gb} GB · {t(`hwKind_${m.kind}`)}
                    {i === 0 ? ` · ★ ${t("recommended")}` : ""}
                    {!m.fits ? ` · ${t("hwTooBig")}` : ""}
                  </span>
                  {isAdmin && caps.ollama.reachable && m.fits && (
                    <Button size="sm" variant="secondary" onClick={() => void run(() => pullOllama(m.tag))}>
                      {t("install")}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-muted">{t("hwEstimate")}</p>
          </div>
        </Card>
      )}

      <Card title={t("installedTitle")}>
        {models === null ? (
          <Skeleton rows={2} height="h-10" />
        ) : models.length === 0 && detected.length === 0 ? (
          <EmptyState title={t("empty")} hint={t("emptyHint")} />
        ) : (
          <ul className="divide-y divide-border">
            {models.map((m) => (
              <li key={m.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-foreground">{m.name}</span>
                    <Badge color={m.mine ? "green" : m.platform ? "yellow" : "gray"}>
                      {m.mine ? t("ownMine") : m.platform ? t("ownPlatform") : t("ownShared")}
                    </Badge>
                  </div>
                  <div className="truncate font-mono text-xs text-muted">
                    {m.model_identifier}
                    {m.ram_usage_mb ? ` · ${m.ram_usage_mb} MB` : ""}
                  </div>
                </div>
                {(isAdmin || m.mine) && (
                  <div className="flex shrink-0 gap-1">
                    <Button size="sm" variant="secondary" onClick={() => setSharing(sharing === m.id ? null : m.id)}>
                      {t("share")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        if (window.confirm(t("deleteConfirm", { name: m.name })))
                          void run(() => deleteModel(m.id));
                      }}
                    >
                      {t("delete")}
                    </Button>
                  </div>
                )}
                {sharing === m.id && (
                  <div className="basis-full">
                    <ModelSharing modelId={m.id} />
                  </div>
                )}
              </li>
            ))}
            {detected.map((o) => (
              <li key={o.name} className="flex items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <span className="font-mono text-sm">{o.name}</span>
                  <div className="text-xs text-muted">{t("detected")}</div>
                </div>
                <Button size="sm" variant="secondary" onClick={() => void run(() => registerOllama(o.name))}>
                  {t("useModel")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title={t("addTitle")}>
        <div className="mb-4 rounded-lg bg-surface-muted p-3 text-sm">
          <p className="font-semibold text-foreground">{t("generativeTitle")}</p>
          <p className="mt-1 text-muted">{t("generativeBody")}</p>
        </div>

        <div>
          <h3 className="mb-2 text-sm font-semibold text-foreground">{t("extTitle")}</h3>
          <p className="mb-3 text-xs text-muted">{t("extHint")}</p>
          <form onSubmit={addExt} className="grid gap-3 sm:grid-cols-2">
            <Field label={t("name")}>
              <Input value={ext.name} onChange={(e) => setExt({ ...ext, name: e.target.value })} placeholder="GPT-4o" required />
            </Field>
            <Field label={t("extModel")}>
              <Input value={ext.model} onChange={(e) => setExt({ ...ext, model: e.target.value })} placeholder="gpt-4o-mini" required />
            </Field>
            <Field label={t("extUrl")} hint={t("extUrlHint")}>
              <Input value={ext.base_url} onChange={(e) => setExt({ ...ext, base_url: e.target.value })} placeholder="https://api.openai.com/v1" required />
            </Field>
            <Field label={t("extKey")} hint={t("extKeyHint")}>
              <Input type="password" autoComplete="off" value={ext.api_key} onChange={(e) => setExt({ ...ext, api_key: e.target.value })} />
            </Field>
            <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
              <Button type="button" variant="secondary" onClick={() => void testExt()} disabled={!ext.base_url.trim() || !ext.model.trim()}>
                {t("extTest")}
              </Button>
              <Button type="submit" variant="secondary" disabled={!ext.name.trim() || !ext.base_url.trim() || !ext.model.trim()}>
                {t("extAdd")}
              </Button>
              {extMsg && <span className={`text-sm ${extMsg.ok ? "text-success" : "text-danger"}`}>{extMsg.text}</span>}
            </div>
          </form>
        </div>

        {isAdmin && caps?.ollama.configured && (
        <div className="mt-6 border-t border-border pt-4">
          <HfSearch onStarted={() => void load()} onError={setError} />
        </div>
        )}
      </Card>
    </div>
  );
}

export default function ModelsPage() {
  return (
    <RequireAuth>
      <ModelsContent />
    </RequireAuth>
  );
}
