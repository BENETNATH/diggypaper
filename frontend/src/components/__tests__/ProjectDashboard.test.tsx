import { Suspense } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
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
      // minimal ICU: {n, plural, one {…} other {…}} picks one/other, # → n
      const s = String(t).replace(
        /\{(\w+), plural, one \{([^}]*)\} other \{([^}]*)\}\}/g,
        (_m: string, name: string, one: string, other: string) => {
          const n = Number((params ?? {})[name] ?? 0);
          return (n === 1 ? one : other).replace(/#/g, String(n));
        }
      );
      return s.replace(/\{(\w+)\}/g, (m: string, k: string) => String((params ?? {})[k] ?? m));
    },
  };
});
/* eslint-enable @typescript-eslint/no-require-imports */
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
  useRouter: () => ({ replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams(""),
}));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getProject: jest.fn(),
  getOverview: jest.fn(),
  listJobs: jest.fn(),
  listModels: jest.fn(),
  updateProject: jest.fn(),
}));

const mockUseAuth = useAuth as unknown as jest.Mock;
const getProject = api.getProject as jest.Mock;
const getOverview = api.getOverview as jest.Mock;
const listJobs = api.listJobs as jest.Mock;
const listModels = api.listModels as jest.Mock;
const updateProject = api.updateProject as jest.Mock;

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
    expect(await screen.findByRole("heading", { name: /d'analyses 1/ })).toBeInTheDocument();
  });

  it("spec clarte-1 R2: untouched node = 1 badge + 1 action « Lancer » (no arrow), no 0 %, lot line instead", async () => {
    mountWithSearches([untouched]);
    await screen.findByRole("heading", { name: /d'analyses 1/ });
    // the single badge (neutral, not « Initiale ») and the single action
    expect(screen.getByText("À lancer")).toBeInTheDocument();
    expect(screen.queryByText("Initiale")).not.toBeInTheDocument();
    const cta = screen.getByRole("button", { name: "Lancer" });
    expect(cta.closest("a")).toHaveAttribute("href", "/projects/p1/inference?search=s2&new=1");
    expect(cta.textContent).not.toContain("→");
    // the 0 % lines are masked until the first run: the lot line instead
    expect(screen.queryByText("0 %")).not.toBeInTheDocument();
    expect(screen.getByText("Pas encore analysée — 5 articles dans le lot.")).toBeInTheDocument();
    expect(screen.queryByText("Aucune question posée")).not.toBeInTheDocument();
    expect(screen.queryByText("Résultats")).not.toBeInTheDocument();
    expect(screen.queryByText("Relancer")).not.toBeInTheDocument();
    expect(screen.queryByText("Analyser")).not.toBeInTheDocument();
  });

  it("spec clarte-1 R2: launched node shows the percentages again", async () => {
    const launched: api.SearchInfo = { ...untouched, is_default: false, predicted: 5, last_job: { status: "COMPLETED", model: "K" } };
    mountWithSearches([launched]);
    await screen.findByRole("heading", { name: /d'analyses 1/ });
    // card percentages back (5/5 analysed, 0/5 reviewed)
    expect(screen.getByText("analysés (5/5 du lot)")).toBeInTheDocument();
    expect(screen.getByText("relus (0/5 du lot)")).toBeInTheDocument();
    expect(screen.queryByText("Pas encore analysée")).not.toBeInTheDocument();
  });

  it("spec clarte-1 R5: permanent scope micro-line (no tooltip), « — » before the first run, « À relire » not highlighted at 0", async () => {
    mountWithSearches([untouched]);
    await screen.findByRole("heading", { name: /d'analyses 1/ });
    // the scope is a visible line, not a tooltip-only info
    expect(screen.getByText("Analysés, Relus et À relire sont calculés sur toutes les analyses du projet · Articles et Texte intégral sur le corpus.")).toBeInTheDocument();
    expect(screen.queryAllByTitle("sur toutes les analyses du projet")).toHaveLength(0);
    // before any run: « — » instead of « 0 % » for Analysés / Relus
    expect(screen.queryByText("0 %")).not.toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(2);
    // « À relire » = 0: no primary highlight, sub « rien à relire »
    const toReview = screen.getByText("À relire").parentElement;
    expect(toReview).not.toHaveClass("border-primary");
    expect(screen.getByText("rien à relire")).toBeInTheDocument();
  });

  it("spec clarte-1 R5: « À relire » highlighted (primary border) only when > 0, with its sub", async () => {
    mountWithSearches([{ ...untouched, to_review: 4 }]);
    await screen.findByRole("heading", { name: /d'analyses 1/ });
    const toReview = screen.getByText("À relire").parentElement;
    expect(toReview).toHaveClass("border-primary");
    expect(toReview).toHaveTextContent("4 articles");
    // every KPI with its sub (Articles / Texte intégral = « 5 articles » here)
    expect(screen.getAllByText("5 articles").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("rien à relire")).not.toBeInTheDocument();
  });

  it("spec clarte-1 R3: active card = primary border only (no green « active » badge); active circle = ring primary + primary letter", async () => {
    const a: api.SearchInfo = { ...untouched, is_default: true, predicted: 10, lot_size: 10, last_job: { status: "COMPLETED", model: "K" } };
    sessionStorage.setItem("diggy:search:p1", a.id);
    try {
      mountWithSearches([a]);
      await screen.findByRole("heading", { name: /d'analyses 1/ });
      // the green badge « active » is gone: the border-2 border-primary card is the pointer
      expect(screen.queryByText(/activeTag/)).not.toBeInTheDocument();
      const circle = screen.getByText("A", { selector: "span" });
      expect(circle).toHaveClass("ring-primary");
      expect(circle).toHaveClass("text-primary");
      expect(circle).not.toHaveClass("bg-success");
    } finally {
      sessionStorage.removeItem("diggy:search:p1");
    }
  });

  it("spec clarte-1 R3: chain circle is green only with results — neutral for a never-run analysis (consistent with the pill dot)", async () => {
    // b: never run (0 predicted, 0 reviewed, non-default) → neutral, NOT green
    const b: api.SearchInfo = { ...untouched, id: "b", name: "B", is_default: false };
    // c: run (predicted > 0) → green
    const c: api.SearchInfo = {
      ...untouched,
      id: "c",
      name: "C",
      is_default: false,
      predicted: 3,
      last_job: { status: "COMPLETED", model: "K" },
      breakdown: { AUTO_ACCEPTED: 3 },
    };
    mountWithSearches([b, c]);
    await screen.findByRole("heading", { name: /d'analyses 2/ });
    // circle letters are positional: A = b (never run), B = c (run)
    const circleA = screen.getByText("A", { selector: "span" });
    expect(circleA).not.toHaveClass("bg-success");
    expect(circleA).toHaveClass("bg-muted");
    const circleB = screen.getByText("B", { selector: "span" });
    expect(circleB).toHaveClass("bg-success");
    expect(circleB).not.toHaveClass("bg-muted");
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
    expect(await screen.findByText("issue des 5 incertains de « A » ↓")).toBeInTheDocument();
  });

  it("« Outillage optionnel »: compact 2-link reminder, no cards (spec clarte-3 R5)", async () => {
    mount6([mk({ id: "a", name: "A", is_default: true })]);
    const heading = await screen.findByRole("heading", {
      name: "Outillage optionnel — mesurer et comparer les modèles",
    });
    // surfaces policy: bg-surface-muted container, no border, no violet
    const section = heading.closest("section");
    expect(section?.className).toContain("bg-surface-muted");
    expect(section?.className).not.toContain("border");
    expect(section?.className).not.toContain("violet");
    const goldLink = screen.getByRole("link", { name: "Ouvrir le gold standard (0 réf.)" });
    expect(goldLink).toHaveAttribute("href", "/projects/p1/gold");
    const benchLink = screen.getByRole("link", { name: "Ouvrir les benchmarks (0 tirages)" });
    expect(benchLink).toHaveAttribute("href", "/projects/p1/benchmarks");
  });

  it("« Réglages » (lead only): the triage thresholds live on the overview (spec clarte-3 R1)", async () => {
    mount6([mk({ id: "a", name: "A", is_default: true })]);
    expect(await screen.findByRole("heading", { name: "Réglages" })).toBeInTheDocument();
    expect(
      screen.getByText("Appliqué aux nouvelles prédictions. Pour reclasser les résultats existants : relancer l'analyse.")
    ).toBeInTheDocument();
  });

  it("« Réglages »: a failed threshold save rolls back the value and shows the error (audit I-1)", async () => {
    mount6([mk({ id: "a", name: "A", is_default: true })]);
    const accept = (await screen.findByLabelText("Auto-validé si confiance >")) as HTMLSelectElement;
    expect(accept).toHaveValue("0.85");
    updateProject.mockRejectedValueOnce(new Error("500"));
    await fireEvent.change(accept, { target: { value: "0.7" } });
    // optimistic update applied, then the failure is visible (error banner replaces the page)
    await screen.findByText("L'enregistrement des seuils a échoué. Veuillez réessayer.");
    expect(updateProject).toHaveBeenCalledWith("p1", { triage_policy: { accept: 0.7, reject: 0.15 } });
    // retry re-loads the page: the value is rolled back to the server value (0.85)
    fireEvent.click(screen.getByText("Réessayer"));
    await screen.findByRole("heading", { name: "Réglages" });
    expect(screen.getByLabelText("Auto-validé si confiance >")).toHaveValue("0.85");
  });

  it("empty project: onboarding stepper shown, KPIs and accordions hidden", async () => {
    mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
    getProject.mockResolvedValue(project);
    getOverview.mockResolvedValue({ ...overview, articles: 0, fulltext: 0 });
    listJobs.mockResolvedValue([]);
    listModels.mockResolvedValue([{ id: "m1", name: "Kev" } as unknown as api.ModelInfo]);
    render(
      <Suspense fallback={null}>
        <ProjectPage />
      </Suspense>
    );
    expect(await screen.findByText("Importer le corpus")).toBeInTheDocument();
    expect(screen.getByText("Créer une analyse")).toBeInTheDocument();
    expect(screen.getByText("Lancer l'analyse avec un modèle")).toBeInTheDocument();
    expect(screen.getByText("Résultats & relecture")).toBeInTheDocument();
    expect(screen.getByText("après l'import")).toBeInTheDocument();
    expect(screen.queryByText("Articles importés")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Outillage optionnel — mesurer et comparer les modèles")
    ).not.toBeInTheDocument();
  });

  it("onboarding step 3 warns when no model is available", async () => {
    mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
    getProject.mockResolvedValue(project);
    getOverview.mockResolvedValue({ ...overview, articles: 0, fulltext: 0 });
    listJobs.mockResolvedValue([]);
    listModels.mockResolvedValue([]);
    render(
      <Suspense fallback={null}>
        <ProjectPage />
      </Suspense>
    );
    expect(await screen.findByText("Importer le corpus")).toBeInTheDocument();
    expect(screen.getByText(/aucun modèle enregistré/i)).toBeInTheDocument();
  });

  it("blind searches are listed outside the chain", async () => {
    const a = mk({ id: "a", name: "A", is_default: true });
    const blind = mk({ id: "bl", name: "Ebloui", kind: "blind", breakdown: {} });
    mount6([a, blind]);
    await screen.findByRole("heading", { name: /Chaîne d'analyses/ });
    // spec clarte-3: the chain counter says "1 analyse" (singular)
    expect(screen.getByText("1 analyse")).toBeInTheDocument();
    expect(screen.getByText("Analyses spécifiques")).toBeInTheDocument();
    expect(screen.getByText("Ebloui")).toBeInTheDocument();
  });

  it("outside-chain section explains what it is, and each card says why (kind hint)", async () => {
    const a = mk({ id: "a", name: "A", is_default: true });
    const blind = mk({ id: "bl", name: "Ebloui", kind: "blind", breakdown: {} });
    const tuning = mk({ id: "tu", name: "Tuning", kind: "tuning", breakdown: {}, predicted: 0 });
    mount6([a, blind, tuning]);
    expect(await screen.findByText("Analyses spécifiques")).toBeInTheDocument();
    expect(
      screen.getByText("Échantillons à l'aveugle et mises au point : des analyses spécifiques, hors de la chaîne standard.")
    ).toBeInTheDocument();
    expect(
      screen.getByText("À l'aveugle : un échantillon relu sans voir la réponse de l'IA (gold standard non biaisé).")
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Mise au point : compare les variantes (modèles, prompts) sur ce lot avant la réalisation de l'analyse."
      )
    ).toBeInTheDocument();
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

  it("spec clarte-1: kpiGold rewritten to « Gold » in the 4 locales, kpiScopeLine present in the 4", () => {
    for (const m of Object.values(all)) {
      expect(m.project.kpiGold).toBe("Gold");
      expect(m.project.kpiScopeLine).toBeTruthy();
    }
  });
});
