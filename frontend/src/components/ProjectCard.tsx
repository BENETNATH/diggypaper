"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { type Project, type Member, deleteProject, listMembers } from "@/lib/api";
import { isProjectLead } from "@/lib/role";
import { useApiError } from "@/lib/errorMsg";
import { Card, Badge, statusColor } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/Feedback";

/** One project of the list (spec 2026-11-20-projets-quick-actions): the card link,
 *  the delete shortcut (R1/R2, lead only) and the « Partagé » chip + members popup (R3/R4). */
export default function ProjectCard({
  project,
  user,
  onDeleted,
}: {
  project: Project;
  user: { role?: string } | null | undefined;
  /** Reloads the list (page's load()). */
  onDeleted: () => void;
}) {
  const t = useTranslations("projects");
  const tc = useTranslations("common");
  const tt = useTranslations("team");
  const apiError = useApiError();
  const lead = isProjectLead(project, user);

  // R2 — delete confirmation
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  function closeConfirm() {
    if (deleting) return;
    setConfirming(false);
    setDeleteError(null);
  }
  async function confirmDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteProject(project.id);
      setConfirming(false);
      setDeleteError(null);
      onDeleted();
    } catch (e) {
      // show the error in the modal: the user can retry or cancel
      setDeleteError(apiError(e));
      setDeleting(false);
    }
  }

  // R4 — members popup: lazy fetch on first open, cached in state per mounted card
  const [open, setOpen] = useState(false);
  const [members, setMembers] = useState<Member[] | null>(null);
  const fetchStarted = useRef(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  function show() {
    setOpen(true);
    if (fetchStarted.current || members !== null) return;
    fetchStarted.current = true;
    listMembers(project.id)
      .then(setMembers)
      .catch(() => {
        // first failure: do not cache the empty state — retry on next open
        fetchStarted.current = false;
      });
  }
  const hide = () => setOpen(false);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: globalThis.MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) hide();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && hide();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative">
      <Link href={`/projects/${project.id}`} className="block">
        <Card className="transition-shadow duration-fast ease-out hover:shadow-md">
          <div className="flex items-start justify-between gap-2">
            <div>
              <h3 className="font-semibold text-foreground">{project.title}</h3>
              {project.description && (
                <p className="mt-1 text-sm text-muted">{project.description}</p>
              )}
            </div>
            {/* lead: room for the absolute trash button (outside this anchor) */}
            <div className={lead ? "pr-7" : undefined}>
              <Badge color={statusColor(project.status)}>
                {t(`status_${project.status}`)}
              </Badge>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-3 text-sm text-muted">
            <span>{t("articles")}: {project.article_count ?? 0}</span>
            {project.role_in_project && <span>· {project.role_in_project}</span>}
            {project.member_count >= 2 && (
              <div
                ref={wrapRef}
                className="relative"
                onMouseEnter={show}
                onMouseLeave={hide}
              >
                <button
                  type="button"
                  onClick={(e) => {
                    // the chip is inside the card <Link>: clicking it must open the
                    // popup, not follow the anchor
                    e.preventDefault();
                    if (open) hide();
                    else show();
                  }}
                  onFocus={show}
                  onBlur={hide}
                  aria-haspopup="true"
                  aria-expanded={open}
                  className="rounded-full border border-border px-2 py-0.5 text-xs text-muted transition-colors duration-fast ease-out hover:bg-surface-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                >
                  {t("shared")} · {project.member_count}
                </button>
                {open && (
                  <div className="absolute left-0 top-full z-20 mt-1.5 w-56 rounded-lg border border-border bg-surface py-1 shadow-lg">
                    {members === null ? (
                      <div className="px-3 py-1.5">
                        <Skeleton rows={2} height="h-3" />
                      </div>
                    ) : (
                      <ul>
                        {members.map((m) => (
                          <li
                            key={m.user_id}
                            className="flex items-center justify-between gap-3 px-3 py-1.5 text-sm"
                          >
                            <span className="text-foreground">
                              {m.full_name ?? m.email}
                            </span>
                            <span className="text-xs text-muted">
                              {tt(m.role === "LEAD" ? "lead" : "annotator")}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </Card>
      </Link>
      {lead && (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          aria-label={t("deleteProjectTitle")}
          title={t("deleteProjectTitle")}
          className="absolute right-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded-md text-muted transition-colors duration-fast ease-out hover:bg-danger/10 hover:text-danger focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M3 6h18" />
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
            <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            <line x1="10" y1="11" x2="10" y2="17" />
            <line x1="14" y1="11" x2="14" y2="17" />
          </svg>
        </button>
      )}
      {confirming && (
        <Modal
          title={t("deleteProjectTitle")}
          onClose={closeConfirm}
        >
          <p className="text-sm text-muted">
            {t("deleteProjectConfirm", { title: project.title })}
          </p>
          {deleteError && <p className="mt-2 text-sm text-danger">{deleteError}</p>}
          <div className="mt-4 flex justify-end gap-2">
            <Button
              variant="secondary"
              disabled={deleting}
              onClick={closeConfirm}
            >
              {tc("cancel")}
            </Button>
            <Button variant="danger" loading={deleting} onClick={confirmDelete}>
              {tc("delete")}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
