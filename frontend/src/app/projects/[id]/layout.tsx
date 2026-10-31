"use client";

import { ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { cancelInference, deleteProject, getProject, getInference, listJobs, ImportJob, InferenceJob, Project } from "@/lib/api";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { ProgressBar } from "@/components/ui/Feedback";
import { JobProgress } from "@/components/JobProgress";
import { isProjectLead } from "@/lib/role";
import { withSearch } from "@/lib/search";
import { useAuth } from "@/lib/auth";
import { useSearchId, useSearches, useSelectSearch } from "@/lib/useSearch";

const STEPS = [
  { key: "overview", href: "", needsCorpus: false, leadOnly: false, optional: false },
  { key: "import", href: "/import", needsCorpus: false, leadOnly: true, optional: false },
  { key: "searches", href: "/searches", needsCorpus: true, leadOnly: false, optional: false },
  { key: "inference", href: "/inference", needsCorpus: true, leadOnly: true, optional: false },
  { key: "articles", href: "/articles", needsCorpus: true, leadOnly: false, optional: false },
  { key: "team", href: "/team", needsCorpus: false, leadOnly: false, optional: false },
  { key: "gold", href: "/gold", needsCorpus: true, leadOnly: false, optional: true },
  { key: "benchmarks", href: "/benchmarks", needsCorpus: true, leadOnly: true, optional: true },
] as const;

/** Persistent project navigation: breadcrumb + step tabs. */
export default function ProjectLayout({ children }: { children: ReactNode }) {
  const t = useTranslations("project");
  const { user } = useAuth();
  const id = useParams<{ id: string }>().id;
  const pathname = usePathname();
  const [project, setProject] = useState<Project | null>(null);

  // Current search. The layout is rendered on the server: until the client has mounted, we
  // do not read sessionStorage (otherwise the tab links differ between server and client).
  const hookSearchId = useSearchId(id);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const searchId = mounted ? hookSearchId : undefined;
  // The searches change (creation, rename, deletion, analyses): we re-read on every page.
  const { searches, current } = useSearches(id, searchId, pathname);
  const selectSearch = useSelectSearch(id);

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
  useEffect(() => {
    if (!importing) {
      setJob(null);
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
  async function stopAnalysis() {
    if (!window.confirm(t("stopConfirm"))) return;
    try {
      await cancelInference(id);
      setInf(await getInference(id, undefined, true));
    } catch {
      /* the real state is re-read on the next tick */
    }
  }
  async function handleDelete() {
    if (!window.confirm(t("deleteConfirm"))) return;
    try {
      await deleteProject(id);
      window.location.href = "/projects";
    } catch {
      window.alert(t("deleteFailed"));
    }
  }
  const ir = inf?.result ?? {};
  const itotal = typeof ir.total === "number" ? ir.total : 0;
  const idone = typeof ir.done === "number" ? ir.done : 0;

  const r = job?.result ?? {};
  const total = typeof r.total === "number" ? r.total : 0;
  const done = typeof r.done === "number" ? r.done : 0;

  const base = `/projects/${id}`;
  const hasCorpus = (project?.article_count ?? 0) > 0;
  const isLead = isProjectLead(project, user);
  const isAnnotate = pathname.startsWith(`${base}/annotate`);

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
        </nav>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {analysing && (
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
                      onClick={() => void stopAnalysis()}
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
          {project && isLead && (
            <Menu label="⋯">
              <MenuItem onClick={() => void handleDelete()}>
                <span className="text-danger">{t("deleteProject")}</span>
              </MenuItem>
            </Menu>
          )}
        </div>
      </div>

      {importing && (
        <div className="rounded-lg border border-primary/40 bg-primary/5 p-3">
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

      {ft && (
        <div className="rounded-lg border border-primary/40 bg-primary/5 p-3">
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

      {searches && searches.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <label className="flex items-center gap-2 text-muted">
            {t("searchLabel")}
            <select
              value={current?.id ?? ""}
              onChange={(e) => selectSearch(e.target.value)}
              className="max-w-[16rem] rounded border border-border bg-surface p-1 text-sm text-foreground"
            >
              {searches.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {s.kind !== "standard" ? ` (${t(`kind_${s.kind}`)})` : ""} — {s.lot_size}
                </option>
              ))}
            </select>
          </label>
          <Link href={withSearch(`${base}/searches`, searchId)} className="text-primary hover:underline">
            {t("allSearches")} →
          </Link>
        </div>
      )}

      <nav
        aria-label="steps"
        className="-mx-4 flex gap-1 overflow-x-auto border-b border-border px-4 md:mx-0 md:px-0"
      >
        {STEPS.filter((s) => !s.leadOnly || isLead).map((s, i, arr) => {
          const href = `${base}${s.href}`;
          const linkHref = withSearch(href, searchId);
          const active =
            s.href === ""
              ? pathname === base
              : pathname.startsWith(href) || (s.key === "articles" && isAnnotate);
          const label = (s.key as string) === "overview" ? t("overview") : t(s.key);
          const blocked = s.needsCorpus && project !== null && !hasCorpus;
          const cls = `whitespace-nowrap border-b-2 px-3 py-2 text-sm ${
            s.optional ? "italic " : ""
          }${
            active
              ? "border-primary font-semibold text-primary"
              : "border-transparent text-muted hover:text-foreground"
          }`;
          const sep = s.optional && !arr[i - 1]?.optional;
          const tab = blocked ? (
            <span key={s.key} className={`${cls} cursor-not-allowed opacity-50`} title={t("needsCorpus")}>
              {label}
            </span>
          ) : (
            <Link key={s.key} href={linkHref} className={cls} aria-current={active ? "page" : undefined}>
              {label}
            </Link>
          );
          return sep ? (
            <span key={s.key} className="flex items-center">
              <span className="mx-2 h-5 w-px bg-border" aria-hidden title={t("optionalTitle")} />
              {tab}
            </span>
          ) : (
            tab
          );
        })}
      </nav>

      {children}
    </div>
  );
}
