"use client";

import { ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cancelInference, deleteProject, getOverview, getProject, getInference, listJobs, listWatches, ImportJob, InferenceJob, Project, ProjectOverview } from "@/lib/api";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { ProgressBar, Toast } from "@/components/ui/Feedback";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { JobProgress } from "@/components/JobProgress";
import { SearchPills } from "@/components/SearchPills";
import { orderChain } from "@/lib/chain";
import { isProjectLead } from "@/lib/role";
import { withSearch } from "@/lib/search";
import { useAuth } from "@/lib/auth";
import { storeSearchId, useSearchId, useSearches } from "@/lib/useSearch";
import { useHasMounted } from "@/lib/useHasMounted";

// spec clarte-2 R1: two explicit groups. `core` = the ordered 4-step path (numbered 1-4,
// decorative aria-hidden); `measure` = the optional « Mesure & veille » group (counters,
// visible legend instead of the old italic + tooltip). `needsCorpus` is kept only as
// information for the pages' EmptyState rendering — never to grey out a tab (R3).
type Step = { key: string; href: string; needsCorpus: boolean; leadOnly: boolean };
const STEPS: { core: Step[]; measure: Step[] } = {
  core: [
    { key: "overview", href: "", needsCorpus: false, leadOnly: false },
    { key: "import", href: "/import", needsCorpus: false, leadOnly: true },
    { key: "inference", href: "/inference", needsCorpus: true, leadOnly: false },
    { key: "articles", href: "/articles", needsCorpus: true, leadOnly: false },
  ],
  measure: [
    { key: "gold", href: "/gold", needsCorpus: true, leadOnly: false },
    { key: "benchmarks", href: "/benchmarks", needsCorpus: true, leadOnly: true },
    { key: "watches", href: "/watches", needsCorpus: false, leadOnly: true },
  ],
};

/** One step tab: a decorative number (core group only) + label, or a counter badge (measure group). */
function StepTab({
  step,
  num,
  count,
  countKey,
  href,
  active,
}: {
  step: Step;
  num?: number;
  count?: number;
  countKey?: string;
  href: string;
  active: boolean;
}) {
  const t = useTranslations("project");
  return (
    <Link
      href={href}
      className={`flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-sm ${
        active ? "border-primary font-semibold text-primary" : "border-transparent text-muted hover:text-foreground"
      }`}
      aria-current={active ? "page" : undefined}
    >
      {num !== undefined && (
        <span
          aria-hidden
          className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold ${
            active ? "border-primary/40 text-primary" : "border-border text-muted"
          }`}
        >
          {num}
        </span>
      )}
      {t(step.key)}
      {count !== undefined && count > 0 && countKey && (
        <span className="rounded-full bg-surface-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
          {t(countKey, { n: count })}
        </span>
      )}
    </Link>
  );
}

/** Persistent project navigation: breadcrumb + step tabs. */
export default function ProjectLayout({ children }: { children: ReactNode }) {
  const t = useTranslations("project");
  const tc = useTranslations("common");
  const { user } = useAuth();
  const id = useParams<{ id: string }>().id;
  const pathname = usePathname();
  const router = useRouter();
  const [project, setProject] = useState<Project | null>(null);

  // Current search. The layout is rendered on the server: until the client has mounted, we
  // do not read sessionStorage (otherwise the tab links differ between server and client).
  const hookSearchId = useSearchId(id);
  const mounted = useHasMounted();
  const searchId = mounted ? hookSearchId : undefined;
  // The searches change (creation, rename, deletion, analyses): we re-read on every page.
  const { searches } = useSearches(id, searchId, pathname);

  // Reload on every page change: the status evolves (import → corpus frozen).
  useEffect(() => {
    getProject(id).then(setProject).catch(() => setProject(null));
  }, [id, pathname]);

  // The Import page pushes the project's updated state (status PROCESSING → FROZEN) without changing the URL.
  useEffect(() => {
    const onProject = (e: Event) => {
      const p = (e as CustomEvent<Project>).detail;
      if (p?.id === id) setProject(p);
    };
    window.addEventListener("diggy:project", onProject);
    return () => window.removeEventListener("diggy:project", onProject);
  }, [id]);

  // Import in progress: progress banner visible on every project page.
  const [job, setJob] = useState<ImportJob | null>(null);
  const importing = project?.status === "PROCESSING";
  // When the import ends, drop the banner job (kept until the status is no longer PROCESSING).
  const [prevImporting, setPrevImporting] = useState(importing);
  if (prevImporting !== importing) {
    setPrevImporting(importing);
    if (!importing) setJob(null);
  }
  useEffect(() => {
    if (!importing) {
      return;
    }
    let stopped = false;
    const tick = async () => {
      try {
        const [js, p] = await Promise.all([listJobs(id), getProject(id)]);
        if (stopped) return;
        setJob(js.find((j) => j.kind !== "fulltext" && (j.status === "PENDING" || j.status === "RUNNING")) ?? null);
        setProject(p); // the status becomes FROZEN at the end: the banner disappears
      } catch {
        /* retries on the next tick */
      }
    };
    void tick();
    const timer = setInterval(tick, 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [importing, id]);
  // Full-text search (open sources): background job, project already frozen.
  const [ft, setFt] = useState<ImportJob | null>(null);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const js = await listJobs(id);
        if (stopped) return;
        const j = js.find((x) => x.kind === "fulltext" && (x.status === "PENDING" || x.status === "RUNNING")) ?? null;
        setFt(j);
        if (j) timer = setTimeout(tick, 3000);
      } catch {
        /* ignored */
      }
    };
    void tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [id, pathname]);
  const fr = ft?.result ?? {};
  const ftotal = typeof fr.total === "number" ? fr.total : 0;
  const fdone = typeof fr.done === "number" ? fr.done : 0;
  const ffound = typeof fr.found === "number" ? fr.found : 0;

  // Analysis (ranking) in progress: same banner, on every project page.
  const [inf, setInf] = useState<InferenceJob | null>(null);
  const analysing = inf?.status === "PENDING" || inf?.status === "RUNNING";
  useEffect(() => {
    let stopped = false;
    const check = async () => {
      try {
        const j = await getInference(id, undefined, true); // the project's analysis in progress
        if (!stopped) setInf(j);
      } catch {
        /* ignored */
      }
    };
    void check();
    if (!analysing && !pathname.endsWith("/inference")) return () => { stopped = true; };
    const timer = setInterval(check, 4000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [id, pathname, analysing]);
  // spec clarte-3 R8: stop + delete confirm through the design-system Modal (was window.confirm/alert).
  const [stopOpen, setStopOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteFailed, setDeleteFailed] = useState(false);
  // deleteFailed → the Toast auto-dismisses after a few seconds.
  useEffect(() => {
    if (!deleteFailed) return;
    const timer = setTimeout(() => setDeleteFailed(false), 5000);
    return () => clearTimeout(timer);
  }, [deleteFailed]);
  async function stopAnalysis() {
    try {
      await cancelInference(id);
      setInf(await getInference(id, undefined, true));
    } catch {
      /* the real state is re-read on the next tick */
    }
  }
  async function handleDelete() {
    setDeleteBusy(true);
    try {
      await deleteProject(id);
      router.push("/projects");
    } catch {
      setDeleteFailed(true);
    } finally {
      setDeleteBusy(false);
    }
  }
  const ir = inf?.result ?? {};
  const itotal = typeof ir.total === "number" ? ir.total : 0;
  const idone = typeof ir.done === "number" ? ir.done : 0;

  const r = job?.result ?? {};
  const total = typeof r.total === "number" ? r.total : 0;
  const done = typeof r.done === "number" ? r.done : 0;

  const base = `/projects/${id}`;
  // Pill order: standards first (chain order), then off-chain (spec clarte-1 R1).
  const standard = (searches ?? []).filter((s) => s.kind === "standard");
  const pillOrder = orderChain(standard).concat((searches ?? []).filter((s) => s.kind !== "standard"));
  const isLead = isProjectLead(project, user);
  const isAnnotate = pathname.startsWith(`${base}/annotate`);

  // spec clarte-2 R1: measure-group counters — gold refs + benchmark draws from the overview,
  // active watches (lead only). Mount + each diggy:project event; no dedicated polling.
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [activeWatches, setActiveWatches] = useState(0);
  useEffect(() => {
    let stopped = false;
    const load = () =>
      void getOverview(id).then((o) => {
        if (!stopped) setOverview(o);
      }).catch(() => {});
    load();
    const onProject = (e: Event) => {
      const p = (e as CustomEvent<Project>).detail;
      if (p?.id === id) load();
    };
    window.addEventListener("diggy:project", onProject);
    return () => {
      stopped = true;
      window.removeEventListener("diggy:project", onProject);
    };
  }, [id]);
  useEffect(() => {
    if (!isLead) return;
    let stopped = false;
    const load = () =>
      void listWatches(id).then((w) => {
        if (!stopped) setActiveWatches(w.filter((x) => x.status === "ACTIVE").length);
      }).catch(() => {});
    load();
    const onProject = (e: Event) => {
      const p = (e as CustomEvent<Project>).detail;
      if (p?.id === id) load();
    };
    window.addEventListener("diggy:project", onProject);
    return () => {
      stopped = true;
      window.removeEventListener("diggy:project", onProject);
    };
  }, [id, isLead]);
  const measureCount: Record<string, number> = {
    gold: overview?.gold ?? 0,
    // same client-side calc as the overview page: analyses with a last benchmark
    benchmarks: (overview?.searches ?? []).filter((s) => s.last_benchmark).length,
    watches: activeWatches,
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <nav aria-label="breadcrumb" className="min-w-0 text-sm text-muted">
          <Link href="/projects" className="hover:text-primary">
            {t("backToProjects")}
          </Link>
          <span className="mx-1">/</span>
          <span className="font-medium text-foreground" title={project?.description ?? undefined}>
            {project?.title ?? "…"}
          </span>
          {project?.description && (
            <span className="line-clamp-1 min-w-0 text-muted">{project.description}</span>
          )}
        </nav>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {/* spec clarte-1 R4: on the overview the ActiveJobsBanner panel replaces these banners */}
          {analysing && pathname !== base && (
            <JobProgress
              inline
              title={t("analysisBannerTitle")}
              done={idone}
              total={itotal}
              segments={{
                accepted: typeof ir.auto_accepted === "number" ? ir.auto_accepted : 0,
                rejected: typeof ir.auto_rejected === "number" ? ir.auto_rejected : 0,
                review: typeof ir.needs_review === "number" ? ir.needs_review : 0,
              }}
              action={
                <span className="flex items-center gap-2">
                  {!pathname.endsWith("/inference") && (
                    <Link href={withSearch(`${base}/inference`, searchId)} className="text-primary hover:underline">
                      {t("detailsLink")}
                    </Link>
                  )}
                  {isLead && (
                    <button
                      type="button"
                      onClick={() => setStopOpen(true)}
                      className="rounded-md border border-danger/40 px-2 py-0.5 text-danger hover:bg-danger/10"
                      title={t("stopTitle")}
                    >
                      ■ {t("stopShort")}
                    </button>
                  )}
                </span>
              }
            />
          )}
          {/* spec clarte-2 R2: the ⋯ menu is visible to every member — Équipe is
              out of the step row and lives here; the destructive action stays lead-only */}
          {project && (
            <Menu label="⋯" ariaLabel={t("menuActions")}>
              <MenuItem href={`${base}/team`}>{t("team")}</MenuItem>
              {isLead && (
                <>
                  <MenuSeparator />
                  <MenuItem onClick={() => setDeleteOpen(true)}>
                    <span className="text-danger">{t("deleteProject")}</span>
                  </MenuItem>
                </>
              )}
            </Menu>
          )}
        </div>
      </div>

      {importing && pathname !== base && (
        <div className="rounded-lg bg-primary/5 p-3">
          <ProgressBar
            value={total > 0 ? (done / total) * 100 : null}
            label={total > 0 ? t("importBanner", { done, total }) : t("importBannerStart")}
          />
          <p className="mt-2 text-xs text-muted">
            {t("importBannerHint")}{" "}
            {!pathname.endsWith("/import") && (
              <Link href={`${base}/import`} className="text-primary hover:underline">
                {t("importBannerLink")} →
              </Link>
            )}
          </p>
        </div>
      )}

      {ft && pathname !== base && (
        <div className="rounded-lg bg-primary/5 p-3">
          <ProgressBar
            value={ftotal > 0 ? (fdone / ftotal) * 100 : null}
            label={ftotal > 0 ? t("fulltextBanner", { done: fdone, total: ftotal, found: ffound }) : t("fulltextBannerStart")}
          />
          <p className="mt-2 text-xs text-muted">
            {t("importBannerHint")}{" "}
            {!pathname.endsWith("/import") && (
              <Link href={`${base}/import`} className="text-primary hover:underline">
                {t("importBannerLink")} →
              </Link>
            )}
          </p>
        </div>
      )}

      {/* spec clarte-2 R1/R3: 4 numbered core steps + separator + visible legend +
          measure group; every tab is a navigable Link (no blocked render), aria-current kept */}
      <nav
        aria-label="steps"
        className="-mx-4 flex gap-1 overflow-x-auto border-b border-border px-4 md:mx-0 md:px-0"
      >
        {STEPS.core.filter((s) => !s.leadOnly || isLead).map((s, i) => (
          <StepTab
            key={s.key}
            step={s}
            num={i + 1}
            href={withSearch(`${base}${s.href}`, searchId)}
            active={
              s.href === ""
                ? pathname === base
                : pathname.startsWith(`${base}${s.href}`) || (s.key === "articles" && isAnnotate)
            }
          />
        ))}
        <span className="flex shrink-0 items-center gap-2 whitespace-nowrap">
          <span aria-hidden className="h-5 w-px bg-border" />
          <span className="text-xs text-muted-foreground">{t("measureLegend")}</span>
        </span>
        {STEPS.measure.filter((s) => !s.leadOnly || isLead).map((s) => (
          <StepTab
            key={s.key}
            step={s}
            count={measureCount[s.key]}
            countKey={
              s.key === "gold" ? "counterRefs" : s.key === "benchmarks" ? "counterDraws" : "counterActive"
            }
            href={withSearch(`${base}${s.href}`, searchId)}
            active={pathname.startsWith(`${base}${s.href}`)}
          />
        ))}
      </nav>

      {/* spec clarte-1 R1: the pill row is the single analysis selector (all analyses) */}
      {searches && (
        <SearchPills
          searches={pillOrder}
          activeId={searchId}
          ariaLabel={t("chainPillsAria")}
          label={t("searchesTitle")}
          newHref={isLead ? `${base}/inference?new=1` : undefined}
          onPick={(sid) => {
            storeSearchId(id, sid);
            router.push(withSearch(`${base}/articles`, sid));
          }}
        />
      )}

      {children}

      {deleteFailed && <Toast message={t("deleteFailed")} onDismiss={() => setDeleteFailed(false)} />}
      {stopOpen && (
        <Modal title={t("stopShort")} onClose={() => setStopOpen(false)}>
          <div className="flex flex-col gap-4">
            <p className="text-sm text-muted">{t("stopConfirm")}</p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setStopOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button variant="danger" size="sm" onClick={() => {
                setStopOpen(false);
                void stopAnalysis();
              }}>
                {t("stopShort")}
              </Button>
            </div>
          </div>
        </Modal>
      )}
      {deleteOpen && (
        <Modal title={t("deleteProject")} onClose={() => setDeleteOpen(false)}>
          <div className="flex flex-col gap-4">
            <p className="text-sm text-muted">{t("deleteConfirm")}</p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setDeleteOpen(false)}>
                {tc("cancel")}
              </Button>
              <Button variant="danger" size="sm" loading={deleteBusy} onClick={() => void handleDelete()}>
                {tc("delete")}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
