"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { deleteGold, getProject, goldFromReviews, listGold, GoldArticle, SchemaField } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { isProjectLead } from "@/lib/role";
import { withSearch } from "@/lib/search";
import { useSearchId, useSearches } from "@/lib/useSearch";
import { RequireAuth } from "@/components/RequireAuth";
import { Button } from "@/components/ui/Button";
import { Badge, Card } from "@/components/ui/Card";
import { Skeleton, ErrorBanner, EmptyState } from "@/components/ui/Feedback";

/** A reference is "relevant" from this score (same threshold as the metrics computation). */
const RELEVANT_AT = 0.5;
/** Below this, the gold is too small to measure anything reliable. */
const MIN_GOLD = 30;

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className={`rounded-lg p-2 text-center ${tone ?? "bg-surface-muted"}`}>
      <div className="text-lg font-bold text-foreground">{value.toLocaleString()}</div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  );
}

function GoldContent() {
  const t = useTranslations("gold");
  const ta = useTranslations("articles");
  const locale = useLocale();
  const projectId = useParams<{ id: string }>().id;
  const searchId = useSearchId(projectId);
  const { searches } = useSearches(projectId, searchId);

  const [gold, setGold] = useState<GoldArticle[] | null>(null);
  const { user } = useAuth();
  const role = user?.role;
  const [lead, setLead] = useState(false);
  const [fields, setFields] = useState<SchemaField[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // "all" or the id of the current row

  const load = useCallback(() => {
    return Promise.all([listGold(projectId, searchId), getProject(projectId, searchId)])
      .then(([g, p]) => {
        setFields(p.fields ?? []);
        setLead(isProjectLead(p, { role }));
        setGold(g);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "error"));
  }, [projectId, searchId, role]);

  // Reset the dataset when the search changes (kept null while it reloads).
  const searchKey = `${projectId}:${searchId}`;
  const [prevSearchKey, setPrevSearchKey] = useState(searchKey);
  if (prevSearchKey !== searchKey) {
    setPrevSearchKey(searchKey);
    setGold(null);
  }

  useEffect(() => {
    load();
  }, [load]);

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    setNote(null);
    try {
      await fn();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setBusy(null);
    }
  }

  const addAll = () =>
    act("all", async () => {
      const r = await goldFromReviews(projectId, {}, searchId);
      setNote(t("addAllResult", r));
    });

  const refresh = (g: GoldArticle) =>
    act(g.id, () => goldFromReviews(projectId, { articleIds: [g.article_id!], refresh: true }, g.search_id ?? undefined));

  const remove = (g: GoldArticle) => {
    if (!window.confirm(t("removeConfirm"))) return;
    void act(g.id, () => deleteGold(projectId, g.id));
  };

  const answer = (v: unknown): string => (typeof v === "boolean" ? (v ? ta("yes") : ta("no")) : String(v ?? "—"));

  if (error && !gold) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!gold) return <Skeleton rows={3} />;

  const relevant = gold.filter((g) => Number(g.ground_truth_json.confidence_score ?? 0) >= RELEVANT_AT).length;
  const bySource = (pred: (g: GoldArticle) => boolean) => gold.filter(pred).length;
  const initialId = searches?.find((s) => s.is_default)?.id;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
        <Button loading={busy === "all"} onClick={() => void addAll()}>
          {t("addAll")}
        </Button>
      </div>
      <p className="text-sm text-muted">{t("intro")}</p>

      {error && <ErrorBanner message={error} />}
      {note && (
        <p className="rounded-lg bg-success/10 p-3 text-sm text-foreground" role="status">
          {note}
        </p>
      )}

      {gold.length > 0 && (
        <Card title={t("countersTitle")}>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label={t("countTotal")} value={gold.length} />
            <Stat label={t("countRelevant")} value={relevant} tone="bg-success/10" />
            <Stat label={t("countNotRelevant")} value={gold.length - relevant} tone="bg-danger/10" />
            <Stat label={t("countValidated")} value={bySource((g) => g.source?.status === "VALIDATED")} />
            <Stat label={t("countCorrected")} value={bySource((g) => g.source?.status === "CORRECTED")} tone="bg-warning/10" />
            <Stat label={t("countBlind")} value={bySource((g) => !!g.source?.blind)} />
          </div>
          {gold.length < MIN_GOLD && (
            <p className="mt-3 rounded-lg bg-warning/10 p-3 text-sm text-foreground">
              ⚠ {t("fewWarning", { n: gold.length, min: MIN_GOLD })}
            </p>
          )}
        </Card>
      )}

      {gold.length === 0 ? (
        <EmptyState title={t("empty")} hint={t("emptyHint")} />
      ) : (
        <Card>
          <ul className="divide-y divide-border">
            {gold.map((g) => (
              <li key={g.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">{g.article_title ?? g.article_id}</p>
                  <p className="text-xs text-muted">
                    {fields.map((f) => `${f.label} : ${answer(g.ground_truth_json[f.key])}`).join(" · ")}
                  </p>
                  <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                    {g.source ? (
                      <>
                        <span>
                          {t("reviewedBy", { name: g.source.by ?? "?" })}
                          {g.source.at ? ` · ${new Date(g.source.at).toLocaleDateString(locale)}` : ""}
                        </span>
                        {g.source.status && (
                          <Badge color={g.source.status === "CORRECTED" ? "yellow" : "green"}>
                            {t(`status_${g.source.status}`)}
                          </Badge>
                        )}
                        {g.source.blind && <Badge color="blue">{t("blindBadge")}</Badge>}
                      </>
                    ) : (
                      <Badge>{t("status_MANUAL")}</Badge>
                    )}
                    {g.stale && <Badge color="yellow">{t("staleBadge")}</Badge>}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  {g.stale && (
                    <Button size="sm" loading={busy === g.id} onClick={() => void refresh(g)}>
                      {t("update")}
                    </Button>
                  )}
                  {g.article_id && (
                    <Link
                      href={withSearch(
                        `/projects/${projectId}/annotate?article=${g.article_id}`,
                        g.search_id ?? initialId ?? searchId
                      )}
                      className="text-sm text-primary hover:underline"
                    >
                      {t("seeSource")}
                    </Link>
                  )}
                  {(lead || g.added_by === user?.id) && (
                    <Button size="sm" variant="danger" disabled={busy === g.id} onClick={() => remove(g)}>
                      {t("remove")}
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

export default function GoldPage() {
  return (
    <RequireAuth>
      <GoldContent />
    </RequireAuth>
  );
}
