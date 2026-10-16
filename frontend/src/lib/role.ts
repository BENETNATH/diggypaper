import type { Project } from "@/lib/api";

/** Responsable du projet : membre LEAD, ou administrateur de l'instance (même règle que le serveur). */
export function isProjectLead(project: Pick<Project, "role_in_project"> | null | undefined, user: { role?: string } | null | undefined): boolean {
  return user?.role === "ADMIN" || project?.role_in_project === "LEAD";
}
