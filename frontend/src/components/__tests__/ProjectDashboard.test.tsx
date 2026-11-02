import { Suspense } from "react";
import { render, screen } from "@testing-library/react";
import ProjectPage from "@/app/projects/[id]/page";
import * as api from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Catalog = Record<string, Record<string, string>>;

// The real fr messages: the spec checks that the renamed keys render "Analyses" (fr).
// (require: the mock factory runs before this module's imports are initialized)
/* eslint-disable @typescript-eslint/no-require-imports */
jest.mock("next-intl", () => {
  const fr = require("../../messages/fr.json") as Catalog;
  return {
    useLocale: () => "fr",
    useTranslations: (ns: string) => (key: string, params?: Record<string, unknown>) => {
      const t = (fr[ns] ?? {})[key];
      if (t == null) return key;
      return String(t).replace(/\{(\w+)\}/g, (m: string, k: string) =>
        String((params ?? {})[k] ?? m)
      );
    },
  };
});
/* eslint-enable @typescript-eslint/no-require-imports */
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
  useRouter: () => ({ replace: jest.fn() }),
}));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getProject: jest.fn(),
  getOverview: jest.fn(),
  listJobs: jest.fn(),
  listModels: jest.fn(),
}));

const mockUseAuth = useAuth as unknown as jest.Mock;
const getProject = api.getProject as jest.Mock;
const getOverview = api.getOverview as jest.Mock;
const listJobs = api.listJobs as jest.Mock;
const listModels = api.listModels as jest.Mock;

const project = {
  id: "p1",
  title: "P",
  role_in_project: "LEAD",
  status: "FROZEN",
  question_set: false,
  retention_delete_on: null,
};
const overview = {
  articles: 5,
  fulltext: 5,
  analysed: 0,
  reviewed: 0,
  gold: 0,
  searches: [],
};

function mountDashboard() {
  mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
  getProject.mockResolvedValue(project);
  getOverview.mockResolvedValue(overview);
  listJobs.mockResolvedValue([]);
  listModels.mockResolvedValue([{ id: "m1", name: "Kev" } as unknown as api.ModelInfo]);
  return render(
    <Suspense fallback={null}>
      <ProjectPage />
    </Suspense>
  );
}

describe("project dashboard (spec 2026-11-02-legacy-questions §7)", () => {
  it("corpus ready, no analysis: ONE button to the Analyse tab, the empty-state CTA with ?new=1", async () => {
    mountDashboard();
    const cta = await screen.findByRole("button", { name: /Lancer une analyse/ });
    expect(cta.closest("a")).toHaveAttribute("href", "/projects/p1/inference?new=1");
    // the duplicated header button (« + Nouvelle question ») is gone
    expect(screen.queryByText("Nouvelle question")).not.toBeInTheDocument();
    const analyseLinks = screen
      .getAllByRole("link")
      .filter((a) => a.getAttribute("href")?.startsWith("/projects/p1/inference"));
    expect(analyseLinks).toHaveLength(1);
    // the list title now reads "Analyses" (fr)
    expect(screen.getByRole("heading", { name: /Analyses/ })).toBeInTheDocument();
  });

  it("list vocabulary in the 4 locales", () => {
    /* eslint-disable @typescript-eslint/no-require-imports */
    const all = {
      fr: require("../../messages/fr.json"),
      en: require("../../messages/en.json"),
      de: require("../../messages/de.json"),
      es: require("../../messages/es.json"),
    };
    /* eslint-enable @typescript-eslint/no-require-imports */
    for (const m of Object.values(all)) {
      expect(m.project.searchesTitle).toBeDefined();
      expect(m.searches.title).toBeDefined();
    }
    expect(all.fr.project.searchesTitle).toBe("Analyses");
    expect(all.en.project.searchesTitle).toBe("Analyses");
    expect(all.de.project.searchesTitle).toBe("Analysen");
    expect(all.es.project.searchesTitle).toBe("Análisis");
    expect(all.fr.searches.title).toBe("Analyses");
    expect(all.en.searches.title).toBe("Analyses");
    expect(all.de.searches.title).toBe("Analysen");
    expect(all.es.searches.title).toBe("Análisis");
    // scopeNewHint (inference namespace): the list name in the 4 locales
    expect(all.fr.inference.scopeNewHint).toContain("Toutes les analyses");
    expect(all.en.inference.scopeNewHint).not.toMatch(/\bquestions\b/i);
    expect(all.de.inference.scopeNewHint).not.toMatch(/\bFragen\b/i);
    expect(all.es.inference.scopeNewHint).not.toMatch(/\bpreguntas\b/i);
  });
});
