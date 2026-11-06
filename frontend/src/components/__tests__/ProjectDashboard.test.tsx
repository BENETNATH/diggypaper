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

// spec 2026-11-02-frontend-vocabulary-a11y-polish §4, §7
describe("project dashboard — to-launch + KPI scope", () => {
  const untouched: api.SearchInfo = {
    id: "s2",
    name: "S2",
    kind: "standard",
    is_default: true,
    question: "q?",
    fields: [],
    text_sections: null,
    parent: null,
    parent_spec: null,
    created_at: null,
    created_by: null,
    lot_size: 5,
    predicted: 0,
    reviewed: 0,
    breakdown: {},
    to_review: 0,
    last_job: null,
    last_benchmark: null,
  };

  function mountWithSearches(searches: api.SearchInfo[]) {
    mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
    getProject.mockResolvedValue(project);
    getOverview.mockResolvedValue({ ...overview, searches });
    listJobs.mockResolvedValue([]);
    listModels.mockResolvedValue([{ id: "m1", name: "Kev" } as unknown as api.ModelInfo]);
    return render(
      <Suspense fallback={null}>
        <ProjectPage />
      </Suspense>
    );
  }

  it("counter « Analyses N » counts the to-launch analysis too", async () => {
    mountWithSearches([untouched]);
    expect(await screen.findByRole("heading", { name: /Analyses 1/ })).toBeInTheDocument();
  });

  it("untouched card: badge « À lancer » + single CTA « Lancer → », no Résultats/Relire/Relancer", async () => {
    mountWithSearches([untouched]);
    await screen.findByRole("heading", { name: /Analyses 1/ });
    expect(screen.getByText("À lancer")).toBeInTheDocument();
    const cta = screen.getByRole("button", { name: "Lancer →" });
    expect(cta.closest("a")).toHaveAttribute("href", "/projects/p1/inference?search=s2&new=1");
    expect(screen.queryByText("Résultats")).not.toBeInTheDocument();
    expect(screen.queryByText("Relancer")).not.toBeInTheDocument();
    expect(screen.queryByText("Analyser")).not.toBeInTheDocument();
  });

  it("KPI tooltip kpiScopeAll on the 3 aggregated KPIs", async () => {
    mountWithSearches([untouched]);
    await screen.findByRole("heading", { name: /Analyses 1/ });
    expect(screen.getAllByTitle("sur toutes les analyses du projet")).toHaveLength(3);
  });
});

// spec v4 R6 — vue d'ensemble : chaîne d'analyses + outillage optionnel
describe("project dashboard — spec v4 R6: chaîne + outillage optionnel", () => {
  const mk = (over: Partial<api.SearchInfo>): api.SearchInfo => ({
    id: "x",
    name: "X",
    kind: "standard",
    is_default: false,
    question: "q?",
    fields: [],
    text_sections: null,
    parent: null,
    parent_spec: null,
    created_at: "2026-11-01T00:00:00Z",
    created_by: null,
    lot_size: 10,
    predicted: 10,
    reviewed: 0,
    breakdown: { AUTO_ACCEPTED: 6, NEEDS_HUMAN_REVIEW: 4 },
    to_review: 4,
    last_job: { status: "COMPLETED", model: "K" },
    last_benchmark: null,
    ...over,
  });

  function mount6(searches: api.SearchInfo[]) {
    mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
    getProject.mockResolvedValue(project);
    getOverview.mockResolvedValue({ ...overview, searches });
    listJobs.mockResolvedValue([]);
    listModels.mockResolvedValue([{ id: "m1", name: "Kev" } as unknown as api.ModelInfo]);
    return render(
      <Suspense fallback={null}>
        <ProjectPage />
      </Suspense>
    );
  }

  it("chain connector « issue des N incertains de X » between linked standard nodes", async () => {
    const a = mk({ id: "a", name: "A", is_default: true, created_at: "2026-11-01T00:00:00Z" });
    const b = mk({
      id: "b",
      name: "B",
      created_at: "2026-11-02T00:00:00Z",
      parent: { id: "a", name: "A" },
      parent_spec: { status: "NEEDS_HUMAN_REVIEW" },
      lot_size: 5,
    });
    mount6([a, b]);
    expect(await screen.findByText("issue des 5 incertains de « A »")).toBeInTheDocument();
  });

  it("« Outillage optionnel · hors chaîne »: gold + benchmarks cards with their state", async () => {
    mount6([mk({ id: "a", name: "A", is_default: true })]);
    await screen.findByRole("heading", { name: /Analyses/ });
    expect(screen.getByText("Outillage optionnel · hors chaîne")).toBeInTheDocument();
    const goldLink = screen.getByRole("link", { name: /Gold/ });
    expect(goldLink).toHaveAttribute("href", "/projects/p1/gold");
    expect(screen.getByText("0 références")).toBeInTheDocument();
    const benchLink = screen.getByRole("link", { name: /Benchmarks/ });
    expect(benchLink).toHaveAttribute("href", "/projects/p1/benchmarks");
  });

  it("blind searches are listed outside the chain", async () => {
    const a = mk({ id: "a", name: "A", is_default: true });
    const blind = mk({ id: "bl", name: "Ebloui", kind: "blind", breakdown: {} });
    mount6([a, blind]);
    await screen.findByRole("heading", { name: /Analyses 2/ });
    expect(screen.getByText("Hors chaîne")).toBeInTheDocument();
    expect(screen.getByText("Ebloui")).toBeInTheDocument();
  });
});

// spec 2026-11-02-frontend-vocabulary-a11y-polish — copy pins (valeur FR attendue)
describe("copy pins (FR values)", () => {
  /* eslint-disable @typescript-eslint/no-require-imports */
  const all = {
    fr: require("../../messages/fr.json"),
    en: require("../../messages/en.json"),
    de: require("../../messages/de.json"),
    es: require("../../messages/es.json"),
  };
  /* eslint-enable @typescript-eslint/no-require-imports */

  it("searches.deleteConfirm preserves the gold references (« préservées »)", () => {
    expect(all.fr.searches.deleteConfirm).toContain("préservées");
    for (const m of Object.values(all)) expect(m.searches.deleteConfirm).toBeTruthy();
  });

  it("articles.startReview = « Relire »", () => {
    expect(all.fr.articles.startReview).toBe("Relire");
  });

  it("common.versionNotRecordedHelp present in the 4 locales", () => {
    for (const m of Object.values(all)) expect(m.common.versionNotRecordedHelp).toBeTruthy();
  });

  it("project.searchLabel = « Analyse : »", () => {
    expect(all.fr.project.searchLabel).toBe("Analyse :");
  });
});
