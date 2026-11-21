"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { useApiError } from "@/lib/errorMsg";
import { useAuth } from "@/lib/auth";
import {
  updateProfile,
  changePassword,
  exportMyData,
  anonymizeMe,
  generateApiKey,
  revokeApiKey,
} from "@/lib/api";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Badge, Card } from "@/components/ui/Card";
import { ErrorBanner } from "@/components/ui/Feedback";
import { Modal } from "@/components/ui/Modal";

/** ISO date → DD/MM/YYYY in the displayed locale ("" if missing/invalid). */
function formatCreated(iso: string | null | undefined, locale: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : new Intl.DateTimeFormat(locale, { day: "2-digit", month: "2-digit", year: "numeric" }).format(d);
}

function ProfileContent() {
  const t = useTranslations("profile");
  const tc = useTranslations("common");
  const apiError = useApiError();
  const { user, refresh, logout } = useAuth();
  const locale = useLocale();
  const [name, setName] = useState("");
  const [lang, setLang] = useState("fr");
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // audit 2026-11-10 P3-14: anonymization confirms through the Modal (was window.confirm).
  const [anonOpen, setAnonOpen] = useState(false);
  const [anonBusy, setAnonBusy] = useState(false);
  // Personal API key (spec 2026-11-21): the generated key lives only in this state
  // (shown once in the modal) — the API returns it in the 201 body only.
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [genBusy, setGenBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [revokeBusy, setRevokeBusy] = useState(false);

  // Seed the form from the logged-in user when they (or the displayed locale) change.
  const seedKey = user ? `${user.id}:${locale}` : "";
  const [prevSeed, setPrevSeed] = useState(seedKey);
  if (prevSeed !== seedKey) {
    setPrevSeed(seedKey);
    if (user) {
      setName(user.full_name ?? "");
      setLang(locale); // language actually displayed (not the one stored at invitation)
    }
  }

  async function run(fn: () => Promise<unknown>, okMsg: string) {
    setError(null);
    setMsg(null);
    try {
      await fn();
      setMsg(okMsg);
    } catch (e) {
      setError(apiError(e));
    }
  }

  async function saveProfile(e: React.FormEvent) {
    e.preventDefault();
    await run(async () => {
      await updateProfile({ full_name: name, language_preference: lang });
      document.cookie = `DP_LOCALE=${lang}; path=/; max-age=31536000; samesite=lax`;
      await refresh();
      if (lang !== locale) window.location.reload(); // applies the language
    }, t("saved"));
  }

  async function savePassword(e: React.FormEvent) {
    e.preventDefault();
    await run(async () => {
      await changePassword(cur, next);
      setCur("");
      setNext("");
    }, t("passwordChanged"));
  }

  async function download() {
    await run(async () => {
      const data = await exportMyData();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "diggypaper-export.json";
      a.click();
      URL.revokeObjectURL(url);
    }, t("exported"));
  }

  function anonymize() {
    setAnonOpen(true);
  }
  async function confirmAnon() {
    setAnonBusy(true);
    setError(null);
    try {
      await anonymizeMe();
      logout();
      setAnonOpen(false);
    } catch (e) {
      setError(apiError(e));
    } finally {
      setAnonBusy(false);
    }
  }

  async function generateKey() {
    setGenBusy(true);
    setError(null);
    try {
      const data = await generateApiKey();
      setCopied(false);
      setApiKey(data.api_key);
    } catch (e) {
      setError(apiError(e) || t("apiError"));
    } finally {
      setGenBusy(false);
    }
  }

  function closeKeyModal() {
    setApiKey(null);
    void refresh(); // the user now has a key: the card switches to "key active"
  }

  function copyKey() {
    if (!apiKey) return;
    void navigator.clipboard.writeText(apiKey).then(() => setCopied(true));
  }

  async function confirmRevoke() {
    setRevokeBusy(true);
    setError(null);
    try {
      await revokeApiKey();
      setRevokeOpen(false);
      await refresh(); // back to the no-key state
    } catch (e) {
      setError(apiError(e) || t("apiError"));
    } finally {
      setRevokeBusy(false);
    }
  }

  if (!user) return null;

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">
          {user.email} · {user.role === "ADMIN" ? t("roleAdmin") : t("roleResearcher")}
        </p>
      </div>

      {error && <ErrorBanner message={error} />}
      {msg && <p role="status" className="text-sm text-success">✓ {msg}</p>}

      <Card title={t("infoTitle")}>
        <form onSubmit={saveProfile} className="flex flex-col gap-4">
          <Field label={t("name")}>
            <Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
          </Field>
          <Field label={t("language")} hint={t("languageHint")}>
            <Select value={lang} onChange={(e) => setLang(e.target.value)}>
              <option value="fr">Français</option>
              <option value="en">English</option>
              <option value="de">Deutsch</option>
              <option value="es">Español</option>
            </Select>
          </Field>
          <div>
            <Button type="submit">{t("save")}</Button>
          </div>
        </form>
      </Card>

      <Card title={t("passwordTitle")}>
        <form onSubmit={savePassword} className="flex flex-col gap-4">
          <Field label={t("currentPassword")}>
            <Input type="password" value={cur} onChange={(e) => setCur(e.target.value)} required autoComplete="current-password" />
          </Field>
          <Field label={t("newPassword")} hint={t("newPasswordHint")}>
            <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={8} autoComplete="new-password" />
          </Field>
          <div>
            <Button type="submit" variant="secondary" disabled={!cur || next.length < 8}>
              {t("changePassword")}
            </Button>
          </div>
        </form>
      </Card>

      <Card title={t("apiTitle")}>
        {user.api_key_set ? (
          <>
            <div className="mb-3 flex items-center gap-2">
              <Badge color="green">{t("apiActive")}</Badge>
              <span className="text-sm text-muted-foreground">
                {t("apiCreated", { date: formatCreated(user.api_key_created_at, locale) })}
              </span>
            </div>
            <div>
              <Button variant="danger" onClick={() => setRevokeOpen(true)}>
                {t("apiRevoke")}
              </Button>
            </div>
            <p className="mt-2 text-xs text-muted">{t("apiRevokeHint")}</p>
          </>
        ) : (
          <>
            <p className="mb-3 text-sm text-muted">{t("apiDesc")}</p>
            <div>
              <Button loading={genBusy} onClick={() => void generateKey()}>
                {t("apiGenerate")}
              </Button>
            </div>
          </>
        )}
      </Card>

      <Card title={t("dataTitle")}>
        <p className="mb-3 text-sm text-muted">{t("dataHint")}</p>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => void download()}>
            {t("export")}
          </Button>
          <Button variant="danger" onClick={() => void anonymize()}>
            {t("anonymize")}
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted">{t("anonymizeHint")}</p>
      </Card>

      <div>
        <Button variant="ghost" onClick={logout}>
          {t("logout")}
        </Button>
      </div>

      {anonOpen && (
        <Modal title={t("anonymize")} onClose={() => setAnonOpen(false)}>
          <div className="flex flex-col gap-4">
            <p className="text-sm text-muted">{t("anonymizeConfirm")}</p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setAnonOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button variant="danger" size="sm" loading={anonBusy} onClick={() => void confirmAnon()}>
                {t("anonymize")}
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {apiKey && (
        <Modal title={t("apiGeneratedTitle")} onClose={closeKeyModal}>
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-muted p-3">
              <code className="flex-1 break-all font-mono text-xs text-foreground">{apiKey}</code>
              <Button size="sm" variant="secondary" onClick={copyKey}>
                {copied ? t("apiCopied") : t("apiCopy")}
              </Button>
            </div>
            <div
              className="flex gap-2.5 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm text-muted-foreground"
              role="alert"
            >
              <span aria-hidden className="font-bold text-warning">
                !
              </span>
              <span>{t("apiGeneratedWarning")}</span>
            </div>
            <div className="flex justify-end">
              <Button variant="secondary" onClick={closeKeyModal}>
                {t("apiClose")}
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {revokeOpen && (
        <Modal title={t("apiRevokeTitle")} onClose={() => setRevokeOpen(false)}>
          <div className="flex flex-col gap-4">
            <p className="text-sm text-muted">{t("apiRevokeConfirm")}</p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setRevokeOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button variant="danger" size="sm" loading={revokeBusy} onClick={() => void confirmRevoke()}>
                {t("apiRevoke")}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

export default function ProfilePage() {
  return (
    <RequireAuth>
      <ProfileContent />
    </RequireAuth>
  );
}
