"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { inviteMember, listMembers, removeMember, Member } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Card, Badge } from "@/components/ui/Card";
import { ErrorBanner } from "@/components/ui/Feedback";

function TeamContent() {
  const t = useTranslations("team");
  const projectId = useParams<{ id: string }>().id;
  const { user } = useAuth();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Member["role"]>("ANNOTATOR");
  const locale = useLocale();
  const [lang, setLang] = useState(locale); // langue de l'e-mail et de l'interface de l'invité
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; link?: string | null } | null>(null);

  const load = useCallback(async () => {
    try {
      setMembers(await listMembers(projectId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const isLead = user?.role === "ADMIN" || members?.some((m) => m.user_id === user?.id && m.role === "LEAD");

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await inviteMember(projectId, email.trim(), role, lang);
      const key = r.status === "added" ? "added" : "invited";
      setNotice({ text: t(r.email_sent ? `${key}Sent` : `${key}NoMail`, { email }), link: r.link });
      setEmail("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(m: Member) {
    if (!window.confirm(t("removeConfirm", { email: m.email }))) return;
    setError(null);
    try {
      await removeMember(projectId, m.user_id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("intro")}</p>
      </div>
      {error && <ErrorBanner message={error} />}

      {isLead && (
        <Card title={t("inviteTitle")}>
          <form onSubmit={handleInvite} className="grid gap-3 sm:grid-cols-[1fr_auto_auto_auto] sm:items-end">
            <Field label={t("email")}>
              <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="collegue@labo.fr" />
            </Field>
            <Field label={t("role")}>
              <Select value={role} onChange={(e) => setRole(e.target.value as Member["role"])}>
                <option value="ANNOTATOR">{t("annotator")}</option>
                <option value="LEAD">{t("lead")}</option>
              </Select>
            </Field>
            <Field label={t("language")}>
              <Select value={lang} onChange={(e) => setLang(e.target.value)}>
                <option value="fr">Français</option>
                <option value="en">English</option>
                <option value="de">Deutsch</option>
                <option value="es">Español</option>
              </Select>
            </Field>
            <Button type="submit" loading={busy} disabled={!email.trim()}>
              {t("invite")}
            </Button>
          </form>
          <p className="mt-2 text-xs text-muted">{t("roleHelp")} {t("languageHelp")}</p>
          {notice && (
            <div className="mt-3 rounded-lg bg-surface-muted p-3 text-sm">
              <p className="text-foreground">{notice.text}</p>
              {notice.link && (
                <input readOnly value={notice.link} onFocus={(e) => e.currentTarget.select()} className="mt-2 w-full rounded border border-border bg-surface p-2 font-mono text-xs" />
              )}
            </div>
          )}
        </Card>
      )}

      <Card title={t("membersTitle")}>
        <ul className="divide-y divide-border">
          {(members ?? []).map((m) => (
            <li key={m.user_id} className="flex items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">{m.full_name || m.email}</p>
                {m.full_name && <p className="truncate text-xs text-muted">{m.email}</p>}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Badge color={m.role === "LEAD" ? "blue" : "gray"}>{m.role === "LEAD" ? t("lead") : t("annotator")}</Badge>
                {isLead && m.user_id !== user?.id && (
                  <Button size="sm" variant="danger" onClick={() => void handleRemove(m)}>
                    {t("remove")}
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

export default function TeamPage() {
  return (
    <RequireAuth>
      <TeamContent />
    </RequireAuth>
  );
}
