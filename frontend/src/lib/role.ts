import { listProjects, type Project } from "@/lib/api";

/** Project owner: LEAD member, or instance administrator (same rule as the server). */
export function isProjectLead(project: Pick<Project, "role_in_project"> | null | undefined, user: { role?: string } | null | undefined): boolean {
  return user?.role === "ADMIN" || project?.role_in_project === "LEAD";
}

/**
 * The user's role in the project, read from the project list — no ``getProject``
 * call, which 404s on a corpus-less project (spec 2026-11-20-analyses-runs-2 R2).
 */
export async function roleFromList(projectId: string): Promise<Project["role_in_project"]> {
  const projects = await listProjects();
  return projects.find((p) => p.id === projectId)?.role_in_project ?? null;
}
