"use client";

import { ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { getProject, getInference, listJobs, ImportJob, InferenceJob, Project } from "@/lib/api";
import { Badge, statusColor } from "@/components/ui/Card";
import { ProgressBar } from "@/components/ui/Feedback";

const STEPS = [
  { key: "overview", href: "", needsCorpus: false, leadOnly: false, optional: false },
  { key: "import", href: "/import", needsCorpus: false, leadOnly: false, optional: false },
  { key: "inference", href: "/inference", needsCorpus: true, leadOnly: true, optional: false },
  { key: "articles", href: "/articles", needsCorpus: true, leadOnly: false, optional: false },
  { key: "team", href: "/team", needsCorpus: false, leadOnly: false, optional: false },
  { key: "gold", href: "/gold", needsCorpus: true, leadOnly: true, optional: true },
  { key: "benchmarks", href: "/benchmarks", needsCorpus: true, leadOnly: true, optional: true },
  { key: "prompts", href: "/prompts", needsCorpus: true, leadOnly: true, optional: true },
] as const;

/** Navigation persistante du projet : fil d'Ariane + onglets des étapes. */
export default function ProjectLayout({ children }: { children: ReactNode }) {
  const t = useTranslations("project");
  const id = useParams<{ id: string }>().id;
  const pathname = usePathname();
  const [project, setProject] = useState<Project | null>(null);

  // Recharge à chaque changement de page : le statut évolue (import → corpus figé).
  useEffect(() => {
    getProject(id).then(setProject).catch(() => setProject(null));
  }, [id, pathname]);

  // La page Import pousse l'état à jour du projet (statut PROCESSING → FROZEN) sans changer d'URL.
  useEffect(() => {
    const onProject = (e: Event) => {
      const p = (e as CustomEvent<Project>).detail;
      if (p?.id === id) setProject(p);
    };
    window.addEventListener("diggy:project", onProject);
    return () => window.removeEventListener("diggy:project", onProject);
  }, [id]);

  // Import en cours : bandeau de progression visible sur toutes les pages du projet.
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
        setProject(p); // le statut passe à FROZEN à la fin : le bandeau disparaît
      } catch {
        /* réessaie au prochain tick */
      }
    };
    void tick();
    const timer = setInterval(tick, 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [importing, id]);
  // Recherche de textes intégraux (sources ouvertes) : job de fond, projet déjà figé.
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
        /* ignoré */
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

  // Analyse (tri) en cours : même bandeau, sur toutes les pages du projet.
  const [inf, setInf] = useState<InferenceJob | null>(null);
  const analysing = inf?.status === "PENDING" || inf?.status === "RUNNING";
  useEffect(() => {
    let stopped = false;
    const check = async () => {
      try {
        const j = await getInference(id);
        if (!stopped) setInf(j);
      } catch {
        /* ignoré */
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
  const ir = inf?.result ?? {};
  const itotal = typeof ir.total === "number" ? ir.total : 0;
  const idone = typeof ir.done === "number" ? ir.done : 0;

  const r = job?.result ?? {};
  const total = typeof r.total === "number" ? r.total : 0;
  const done = typeof r.done === "number" ? r.done : 0;

  const base = `/projects/${id}`;
  const frozen = project?.status === "FROZEN" || project?.status === "COMPLETED";
  const isLead = project?.role_in_project === "LEAD" || project?.role_in_project == null;
  const isAnnotate = pathname.startsWith(`${base}/annotate`);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <nav aria-label="breadcrumb" className="min-w-0 text-sm text-muted">
          <Link href="/projects" className="hover:text-primary">
            {t("backToProjects")}
          </Link>
          <span className="mx-1">/</span>
          <span className="font-medium text-foreground">{project?.title ?? "…"}</span>
        </nav>
        {project && (
          <Badge color={statusColor(project.status)}>{project.status}</Badge>
        )}
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

      {analysing && (
        <div className="rounded-lg border border-primary/40 bg-primary/5 p-3">
          <ProgressBar
            value={itotal > 0 ? (idone / itotal) * 100 : null}
            label={itotal > 0 ? t("analysisBanner", { done: idone, total: itotal }) : t("analysisBannerStart")}
          />
          <p className="mt-2 text-xs text-muted">
            {t("importBannerHint")}{" "}
            {!pathname.endsWith("/inference") && (
              <Link href={`${base}/inference`} className="text-primary hover:underline">
                {t("importBannerLink")} →
              </Link>
            )}
          </p>
        </div>
      )}

      <nav
        aria-label="steps"
        className="-mx-4 flex gap-1 overflow-x-auto border-b border-border px-4 md:mx-0 md:px-0"
      >
        {STEPS.filter((s) => !s.leadOnly || isLead).map((s, i, arr) => {
          const href = `${base}${s.href}`;
          const active =
            s.href === ""
              ? pathname === base
              : pathname.startsWith(href) || (s.key === "articles" && isAnnotate);
          const label = (s.key as string) === "overview" ? t("overview") : t(s.key);
          const blocked = s.needsCorpus && project !== null && !frozen;
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
            <Link key={s.key} href={href} className={cls} aria-current={active ? "page" : undefined}>
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
