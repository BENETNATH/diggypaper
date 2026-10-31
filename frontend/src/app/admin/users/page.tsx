"use client";

import { useCallback, useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import {
  AdminInvitation,
  AdminProject,
  AdminUser,
  deleteAdminUser,
  deleteProject,
  purgeOrphanUsers,
  inviteCreator,
  listAdminInvitations,
  listAdminProjects,
  listAdminUsers,
  patchAdminUser,
  resendInvitation,
  revokeInvitation,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireAdmin } from "@/components/RequireAdmin";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Badge, Card } from "@/components/ui/Card";
import { ErrorBanner, Skeleton } from "@/components/ui/Feedback";

const LANGS = ["fr", "en", "de", "es"];

function UserRow({
  u,
  me,
  onChange,
  onError,
  onNotice,
}: {
  u: AdminUser;
  me: boolean;
  onChange: () => void;
  onError: (m: string) => void;
  onNotice: (text: string) => void;
}) {
  const t = useTranslations("adminUsers");
  const [edit, setEdit] = useState(false);
  const [del, setDel] = useState(false);
  const [alsoProjects, setAlsoProjects] = useState(false);
  const [name, setName] = useState(u.full_name ?? "");
  const [role, setRole] = useState(u.role);
  const [lang, setLang] = useState(u.language_preference ?? "fr");

  async function run(fn: () => Promise<unknown>) {
    try {
      await fn();
      onChange();
    } catch (e) {
      onError(e instanceof Error ? e.message : "error");
    }
  }

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">
            {u.full_name || u.email} {me && <span className="text-xs text-muted">({t("you")})</span>}
          </p>
          {u.full_name && <p className="truncate text-xs text-muted">{u.email}</p>}
          <p className="text-xs text-muted">
            {t("counts", { projects: u.projects, created: u.created_projects, models: u.models, annotations: u.annotations })}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {u.role === "ADMIN" ? (
            <Badge color="blue">{t("admin")}</Badge>
          ) : (
            <Badge color={u.can_create_projects ? "green" : "gray"}>{u.can_create_projects ? t("creator") : t("guest")}</Badge>
          )}
          {u.anonymized && <Badge color="gray">{t("anonymized")}</Badge>}
          <Badge color={u.is_active ? "green" : "red"}>{u.is_active ? t("active") : t("disabled")}</Badge>
          <Button size="sm" variant="secondary" onClick={() => setEdit(!edit)}>
            {t("edit")}
          </Button>
          {!me && (
            <>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => void run(() => patchAdminUser(u.id, { is_active: !u.is_active }))}
              >
                {u.is_active ? t("disable") : t("enable")}
              </Button>
              {u.role !== "ADMIN" && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void run(() => patchAdminUser(u.id, { can_create_projects: !u.can_create_projects }))}
                >
                  {u.can_create_projects ? t("makeGuest") : t("makeCreator")}
                </Button>
              )}
              <Button size="sm" variant="danger" onClick={() => setDel(!del)}>
                {t("delete")}
              </Button>
            </>
          )}
        </div>
      </div>

      {edit && (
        <form
          className="mt-2 flex flex-wrap items-end gap-2 rounded-lg bg-surface-muted p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await patchAdminUser(u.id, { full_name: name, role, language_preference: lang });
              setEdit(false);
            });
          }}
        >
          <Field label={t("name")} className="w-48">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("role")}>
            <Select value={role} onChange={(e) => setRole(e.target.value as AdminUser["role"])} disabled={me}>
              <option value="RESEARCHER">{t("researcher")}</option>
              <option value="ADMIN">{t("admin")}</option>
            </Select>
          </Field>
          <Field label={t("language")}>
            <Select value={lang} onChange={(e) => setLang(e.target.value)}>
              {LANGS.map((l) => (
                <option key={l} value={l}>
                  {l.toUpperCase()}
                </option>
              ))}
            </Select>
          </Field>
          <Button type="submit" size="sm">
            {t("save")}
          </Button>
        </form>
      )}

      {del && (
        <div className="mt-2 rounded-lg border border-danger/40 bg-danger/5 p-3 text-sm">
          <p className="text-foreground">{t("deleteWarn", { email: u.email })}</p>
          {u.created_projects > 0 && (
            <label className="mt-2 flex items-center gap-2 text-foreground">
              <input type="checkbox" checked={alsoProjects} onChange={(e) => setAlsoProjects(e.target.checked)} />
              {t("deleteProjects", { n: u.created_projects })}
            </label>
          )}
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              variant="danger"
              onClick={() =>
                void run(async () => {
                  const r = await deleteAdminUser(u.id, alsoProjects);
                  if (r.orphaned_projects.length)
                    onNotice(t("orphaned", { list: r.orphaned_projects.join(", ") }));
                })
              }
            >
              {t("deleteConfirm")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDel(false)}>
              {t("cancel")}
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

function AdminContent() {
  const t = useTranslations("adminUsers");
  const { user } = useAuth();
  const locale = useLocale();
  const [inviteEmail, setInviteEmail] = useState("");
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [invites, setInvites] = useState<AdminInvitation[]>([]);
  const [projects, setProjects] = useState<AdminProject[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; link?: string | null } | null>(null);

  const load = useCallback(async () => {
    try {
      const [u, i, p] = await Promise.all([listAdminUsers(), listAdminInvitations(), listAdminProjects()]);
      setUsers(u);
      setInvites(i);
      setProjects(p);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function resend(i: AdminInvitation) {
    setNotice(null);
    try {
      const r = await resendInvitation(i.id);
      setNotice({ text: r.email_sent ? t("resent", { email: i.email }) : t("resentNoMail", { email: i.email }), link: r.link });
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("intro")}</p>
      </div>
      {error && <ErrorBanner message={error} onRetry={() => void load()} />}

      <Card title={t("inviteTitle")}>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setNotice(null);
            void act(async () => {
              const r = await inviteCreator(inviteEmail.trim(), locale);
              setNotice({ text: r.email_sent ? t("inviteSent", { email: inviteEmail }) : t("inviteNoMail", { email: inviteEmail }), link: r.link });
              setInviteEmail("");
            });
          }}
        >
          <Field label={t("inviteEmail")} className="w-72">
            <Input type="email" required value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} />
          </Field>
          <Button type="submit" size="sm">
            {t("inviteCreator")}
          </Button>
        </form>
        <p className="mt-2 text-xs text-muted">{t("inviteHint")}</p>
      </Card>

      <div className="flex items-center gap-3">
        <Button
          size="sm"
          variant="danger"
          onClick={() => {
            if (window.confirm(t("purgeConfirm")))
              void act(async () => {
                const r = await purgeOrphanUsers();
                setNotice({ text: t("purged", { n: r.deleted }) });
              });
          }}
        >
          {t("purgeOrphans")}
        </Button>
        <span className="text-xs text-muted">{t("purgeHint")}</span>
      </div>

      <Card title={t("usersTitle", { n: users?.length ?? 0 })}>
        {users === null ? (
          <Skeleton rows={3} height="h-10" />
        ) : (
          <ul className="divide-y divide-border">
            {users.map((u) => (
              <UserRow
                key={u.id}
                u={u}
                me={u.id === user?.id}
                onChange={() => void load()}
                onError={setError}
                onNotice={(text) => setNotice({ text })}
              />
            ))}
          </ul>
        )}
      </Card>

      <Card title={t("invitesTitle", { n: invites.length })}>
        {notice && (
          <div className="mb-3 rounded-lg bg-surface-muted p-3 text-sm">
            <p className="text-foreground">{notice.text}</p>
            {notice.link && (
              <input readOnly value={notice.link} onFocus={(e) => e.currentTarget.select()} className="mt-2 w-full rounded border border-border bg-surface p-2 font-mono text-xs" />
            )}
          </div>
        )}
        {invites.length === 0 ? (
          <p className="text-sm text-muted">{t("noInvites")}</p>
        ) : (
          <ul className="divide-y divide-border">
            {invites.map((i) => (
              <li key={i.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground">{i.email}</p>
                  <p className="text-xs text-muted">
                    {i.project ? `${i.project} · ${i.role}` : t("platformInvite")} · {i.language.toUpperCase()}
                    {i.invited_by ? ` · ${t("invitedBy", { by: i.invited_by })}` : ""}
                    {i.created_at ? ` · ${new Date(i.created_at).toLocaleDateString()}` : ""}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => void resend(i)}>
                    {t("resend")}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => void act(() => revokeInvitation(i.id))}>
                    {t("revoke")}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title={t("projectsTitle", { n: projects.length })}>
        <ul className="divide-y divide-border">
          {projects.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">{p.title}</p>
                <p className="text-xs text-muted">
                  {p.created_by ?? t("noOwner")} · {t("projectCounts", { members: p.members, articles: p.articles })}
                </p>
              </div>
              <Button
                size="sm"
                variant="danger"
                onClick={() => {
                  if (window.confirm(t("projectDeleteConfirm", { title: p.title }))) void act(() => deleteProject(p.id));
                }}
              >
                {t("delete")}
              </Button>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

export default function AdminUsersPage() {
  return (
    <RequireAuth>
      <RequireAdmin>
        <AdminContent />
      </RequireAdmin>
    </RequireAuth>
  );
}
