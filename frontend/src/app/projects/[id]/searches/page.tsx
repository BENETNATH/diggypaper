"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import {
  createSearch,
  deleteSearch,
  getProject,
  renameSearch,
  SchemaField,
  SearchInfo,
  SearchKind,
  Selection,
} from "@/lib/api";
import { cleanSelection, prefillStoreKey, withSearch } from "@/lib/search";
import { notifySearchesChanged, storeSearchId, useSearchId, useSearches, useSelectSearch } from "@/lib/useSearch";
import { ArticleSelector } from "@/components/ArticleSelector";
import { QuestionEditor } from "@/components/QuestionEditor";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Badge, Card, statusColor } from "@/components/ui/Card";
import { EmptyState, ErrorBanner, Skeleton } from "@/components/ui/Feedback";

const DEFAULT_BLIND_N = 40;

type Definition = { question: string; fields: SchemaField[]; sections: string[]; readWindows: number };

/** Items nettoyés pour l'API (options de choix sans entrées vides). */
const cleanFields = (fields: SchemaField[]): SchemaField[] =>
  fields.map((f) => ({ ...f, options: f.type === "choice" ? (f.options ?? []).filter(Boolean) : undefined }));

function SearchCard({
  s,
  current,
  isLead,
  onOpen,
  onChanged,
  onDeleted,
}: {
  s: SearchInfo;
  current: boolean;
  isLead: boolean;
  onOpen: () => void;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const t = useTranslations("searches");
  const locale = useLocale();
  const projectId = useParams<{ id: string }>().id;
  const [renaming, setRenaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(fn: () => Promise<unknown>, after: () => void) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      after();
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={`rounded-xl border p-4 ${current ? "border-primary bg-primary/5" : "border-border bg-surface"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {renaming === null ? (
            <h3 className="flex flex-wrap items-center gap-2 font-semibold text-foreground">
              {s.name}
              {s.is_default && <Badge color="blue">{t("initial")}</Badge>}
              {s.kind !== "standard" && <Badge color={s.kind === "blind" ? "yellow" : "gray"}>{t(`kind_${s.kind}`)}</Badge>}
              {current && <Badge color="green">{t("current")}</Badge>}
            </h3>
          ) : (
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (renaming.trim())
                  void run(() => renameSearch(projectId, s.id, renaming.trim()), () => {
                    setRenaming(null);
                    onChanged();
                  });
              }}
            >
              <Input value={renaming} onChange={(e) => setRenaming(e.target.value)} aria-label={t("rename")} autoFocus />
              <Button type="submit" size="sm" loading={busy}>
                {t("save")}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setRenaming(null)}>
                {t("cancel")}
              </Button>
            </form>
          )}
          {s.parent && (
            <p className="mt-0.5 text-xs text-muted">
              {t("derivedFrom")} : <span className="text-foreground">{s.parent.name}</span>
            </p>
          )}
          <p className="mt-1 text-sm text-foreground/80">{s.question}</p>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <span>{t("stats", { lot: s.lot_size, predicted: s.predicted, reviewed: s.reviewed })}</span>
            {s.last_job && (
              <span className="inline-flex items-center gap-1">
                {t("lastJob")} <Badge color={statusColor(s.last_job.status)}>{s.last_job.status}</Badge>
                <code>{s.last_job.model}</code>
              </span>
            )}
            {s.created_by && (
              <span>
                {s.created_by}
                {s.created_at ? ` · ${new Date(s.created_at).toLocaleDateString(locale)}` : ""}
              </span>
            )}
          </p>
          {error && <p className="mt-1 text-xs text-danger">{error}</p>}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button size="sm" onClick={onOpen}>
            {t("open")}
          </Button>
          {isLead && renaming === null && (
            <Button size="sm" variant="secondary" onClick={() => setRenaming(s.name)}>
              {t("rename")}
            </Button>
          )}
          {isLead && !s.is_default && (
            <Button
              size="sm"
              variant="danger"
              loading={busy}
              onClick={() => {
                if (window.confirm(t("deleteConfirm", { name: s.name })))
                  void run(() => deleteSearch(projectId, s.id), onDeleted);
              }}
            >
              {t("delete")}
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}

function SearchesContent() {
  const t = useTranslations("searches");
  const projectId = useParams<{ id: string }>().id;
  const router = useRouter();
  const searchId = useSearchId(projectId);
  const { searches, current, reload } = useSearches(projectId, searchId);
  const selectSearch = useSelectSearch(projectId);
  const [isLead, setIsLead] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Formulaire « Nouvelle recherche » (éventuellement prérempli depuis la page Articles).
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [selection, setSelection] = useState<Selection>({});
  const [count, setCount] = useState<number | null>(null);
  const [edit, setEdit] = useState<Definition | null>(null); // null : question copiée telle quelle
  const [originDef, setOriginDef] = useState<Definition | null>(null);
  const [creating, setCreating] = useState(false);

  // Échantillon à l'aveugle
  const [blindN, setBlindN] = useState(DEFAULT_BLIND_N);
  const [blindBusy, setBlindBusy] = useState(false);

  useEffect(() => {
    getProject(projectId)
      .then((p) => setIsLead(p.role_in_project === "LEAD" || p.role_in_project == null))
      .catch(() => {});
    try {
      const raw = sessionStorage.getItem(prefillStoreKey(projectId));
      if (raw) {
        sessionStorage.removeItem(prefillStoreKey(projectId));
        setSelection(JSON.parse(raw) as Selection);
        setShowForm(true);
      }
    } catch {
      /* pas de préremplissage */
    }
  }, [projectId]);

  // Question et items de la recherche d'origine : copiés par défaut, modifiables.
  const originId = selection.from_search_id ?? searches?.find((s) => s.is_default)?.id;
  useEffect(() => {
    if (!originId || !showForm) return;
    setEdit(null);
    getProject(projectId, originId)
      .then((p) =>
        setOriginDef({
          question: p.question ?? "",
          fields: p.fields ?? [],
          sections: p.text_sections ?? ["methods"],
          readWindows: p.read_windows ?? 4,
        })
      )
      .catch(() => setOriginDef(null));
  }, [projectId, originId, showForm]);

  function open(s: SearchInfo) {
    storeSearchId(projectId, s.id);
    router.push(withSearch(`/projects/${projectId}/articles`, s.id));
  }

  async function create(kind: SearchKind, body: { name?: string; selection: Selection; def?: Definition | null }) {
    const s = await createSearch(projectId, {
      name: body.name,
      kind,
      selection: cleanSelection(body.selection),
      ...(body.def
        ? {
            question: body.def.question,
            fields: cleanFields(body.def.fields),
            text_sections: body.def.sections,
            read_windows: body.def.readWindows,
          }
        : {}),
    });
    storeSearchId(projectId, s.id);
    notifySearchesChanged();
    return s;
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const s = await create("standard", { name: name.trim() || undefined, selection, def: edit });
      router.push(withSearch(`/projects/${projectId}/inference`, s.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
      setCreating(false);
    }
  }

  async function handleBlind() {
    setBlindBusy(true);
    setError(null);
    try {
      const from = current && !current.is_default ? current.id : undefined;
      const s = await create("blind", {
        selection: { from_search_id: from, sample_n: blindN, seed: Math.floor(Math.random() * 1e9) },
      });
      router.push(withSearch(`/projects/${projectId}/annotate?queue=all`, s.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
      setBlindBusy(false);
    }
  }

  if (!searches) return error ? <ErrorBanner message={error} onRetry={() => void reload()} /> : <Skeleton rows={3} />;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("intro")}</p>
      </div>

      {error && <ErrorBanner message={error} />}

      {searches.length === 0 ? (
        <EmptyState title={t("empty")} />
      ) : (
        <ul className="flex flex-col gap-3">
          {searches.map((s) => (
            <SearchCard
              key={s.id}
              s={s}
              current={s.id === current?.id}
              isLead={isLead}
              onOpen={() => open(s)}
              onChanged={() => {
                void reload();
                notifySearchesChanged();
              }}
              onDeleted={() => {
                // La recherche supprimée était peut-être la courante : retour à la recherche initiale.
                const initial = searches.find((x) => x.is_default);
                if (s.id === current?.id && initial) selectSearch(initial.id);
                void reload();
                notifySearchesChanged();
              }}
            />
          ))}
        </ul>
      )}

      {isLead ? (
        <>
          <Card title={t("blindTitle")}>
            <p className="text-sm text-muted">{t("blindIntro")}</p>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <Field label={t("blindN")}>
                <Input
                  type="number"
                  min={1}
                  value={blindN}
                  onChange={(e) => setBlindN(Math.max(1, parseInt(e.target.value, 10) || 1))}
                  className="w-28"
                />
              </Field>
              <Button loading={blindBusy} onClick={() => void handleBlind()}>
                {t("blindCreate")}
              </Button>
            </div>
          </Card>

          {!showForm ? (
            <div>
              <Button variant="secondary" onClick={() => setShowForm(true)}>
                + {t("newTitle")}
              </Button>
            </div>
          ) : (
            <Card title={t("newTitle")}>
              <form onSubmit={handleCreate} className="flex flex-col gap-4">
                <Field label={t("nameLabel")}>
                  <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("namePlaceholder")} />
                </Field>
                <ArticleSelector
                  projectId={projectId}
                  searches={searches}
                  value={selection}
                  onChange={setSelection}
                  onCount={setCount}
                />
                <details className="rounded-lg border border-border p-3">
                  <summary className="cursor-pointer text-sm font-medium text-foreground">{t("questionTitle")}</summary>
                  <p className="my-2 text-xs text-muted">{t("questionHint")}</p>
                  {edit ?? originDef ? (
                    <QuestionEditor {...(edit ?? originDef)!} onChange={setEdit} />
                  ) : (
                    <p className="text-sm text-muted">…</p>
                  )}
                </details>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="submit"
                    loading={creating}
                    disabled={!count || (edit?.sections.length === 0)}
                  >
                    {t("create")}
                  </Button>
                  <Button type="button" variant="secondary" onClick={() => setShowForm(false)}>
                    {t("cancel")}
                  </Button>
                  {count === 0 && <span className="text-sm text-warning">{t("emptySelection")}</span>}
                </div>
              </form>
            </Card>
          )}
        </>
      ) : (
        <p className="text-sm text-muted">{t("leadOnly")}</p>
      )}
    </div>
  );
}

export default function SearchesPage() {
  return (
    <RequireAuth>
      <SearchesContent />
    </RequireAuth>
  );
}
