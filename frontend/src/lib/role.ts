import type { Project } from "@/lib/api";

/** Project owner: LEAD member, or instance administrator (same rule as the server). */
export function isProjectLead(project: Pick<Project, "role_in_project"> | null | undefined, user: { role?: string } | null | undefined): boolean {
  return user?.role === "ADMIN" || project?.role_in_project === "LEAD";
}
