import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ProjectLayout from "@/app/projects/[id]/layout";
import * as api from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ProjectOverview, SearchInfo, Watch } from "@/lib/api";

// i18n keys returned as-is (params echoed so counters can be asserted):
// the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string, params?: Record<string, unknown>) => (params ? `${key}(${JSON.stringify(params)})` : key);
  return { useLocale: () => "fr", useTranslations: () => t };
});
const mockState = { pathname: "/projects/p1" };
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
  usePathname: () => mockState.pathname,
  useSearchParams: () => ({ get: () => null, toString: () => "" }),
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getProject: jest.fn(),
  getInference: jest.fn(),
  listJobs: jest.fn(),
  listSearches: jest.fn(),
  cancelInference: jest.fn(),
  deleteProject: jest.fn(),
  getOverview: jest.fn().mockResolvedValue({ articles: 0, fulltext: 0, analysed: 0, reviewed: 0, gold: 0, searches: [] }),
  listWatches: jest.fn().mockResolvedValue([]),
}));

const mockUseAuth = useAuth as unknown as jest.Mock;
const getProject = api.getProject as jest.Mock;
const getInference = api.getInference as jest.Mock;
const listJobs = api.listJobs as jest.Mock;
const listSearches = api.listSearches as jest.Mock;
const getOverview = api.getOverview as jest.Mock;
const listWatches = api.listWatches as jest.Mock;

const mkSearch = (over: Partial<SearchInfo>): SearchInfo => ({
  id: "s1",
  name: "S1",
  kind: "standard",
  is_default: true,
  question: "q?",
  fields: [],
  text_sections: null,
  parent: null,
  parent_spec: null,
  created_at: null,
  created_by: null,
  lot_size: 10,
  predicted: 10,
  reviewed: 0,
  breakdown: {},
  to_review: 0,
  last_job: { status: "COMPLETED", model: "K" },
  last_benchmark: null,
  ...over,
});

const project = {
  id: "p1",
  title: "P1",
  role_in_project: "LEAD",
  status: "PROCESSING",
  question_set: false,
  retention_delete_on: null,
};

function mount(projectOverride?: Partial<api.Project>) {
  mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
  getProject.mockResolvedValue({ ...project, ...projectOverride });
  getInference.mockResolvedValue({ id: "i1", status: "RUNNING", result: null, error: null });
  listJobs.mockResolvedValue([
    {
      id: "j1",
      task: "import_pubmed",
      kind: "import",
      source: "pubmed",
      status: "RUNNING",
      query: "q",
      result: null,
      error: null,
      created_at: null,
      completed_at: null,
    },
    {
      id: "j2",
      task: "import_pubmed",
      kind: "fulltext",
      source: null,
      status: "RUNNING",
      query: null,
      result: null,
      error: null,
      created_at: null,
      completed_at: null,
    },
  ]);
  listSearches.mockResolvedValue([
    mkSearch({ id: "sa", name: "Alpha" }),
    mkSearch({ id: "sb", name: "Beta", kind: "blind", is_default: false, predicted: 0, last_job: null }),
  ]);
  return render(<ProjectLayout><div>child</div></ProjectLayout>);
}

// spec 2026-11-09-clarte-1 — R1 sélecteur unique, R4 bannières masquées sur l'overview
describe("project layout (spec clarte-1)", () => {
  it("R1: no <select>, no « toutes les analyses » link; pills = all analyses, standards first, kind badge off-chain", async () => {
    mockState.pathname = "/projects/p1";
    const { container } = mount();
    await screen.findByRole("button", { name: /Alpha/ });
    // the selectors are gone
    expect(container.querySelector("select")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("link", { name: /allSearches/ })).toBeNull();
    // the row holds ALL analyses: standard first, then the off-chain one with its kind badge
    const pills = screen.getAllByRole("button").filter((b) => b.textContent?.includes("·"));
    expect(pills).toHaveLength(2);
    expect(pills[0].textContent).toContain("Alpha");
    expect(pills[1].textContent).toContain("Beta");
    expect(screen.getByRole("button", { name: /Beta/ })).toHaveTextContent("kind_blind");
    // lead: the dashed « + Nouvelle » pill stays
    expect(screen.getByRole("link", { name: /pillNew/ })).toBeInTheDocument();
  });

  it("R4: with active import + fulltext + analysis jobs, the layout banners are hidden on the overview", async () => {
    mockState.pathname = "/projects/p1";
    mount();
    await screen.findByRole("button", { name: /Alpha/ });
    await waitFor(() => expect(getProject).toHaveBeenCalled());
    await waitFor(() => expect(listJobs).toHaveBeenCalled());
    await waitFor(() => expect(getInference).toHaveBeenCalled());
    await act(async () => {}); // flush the promise resolutions
    expect(screen.queryByText("importBannerStart")).not.toBeInTheDocument();
    expect(screen.queryByText("fulltextBannerStart")).not.toBeInTheDocument();
    expect(screen.queryByText("analysisBannerTitle")).not.toBeInTheDocument();
    expect(screen.getByText("child")).toBeInTheDocument();
  });

  it("R4: on the import page the same banners stay visible", async () => {
    mockState.pathname = "/projects/p1/import";
    mount();
    // import + fulltext banners (the jobs are active)
    expect(await screen.findByText("importBannerStart")).toBeInTheDocument();
    expect(await screen.findByText("fulltextBannerStart")).toBeInTheDocument();
    // analysis banner (JobProgress) too
    expect(await screen.findByText("analysisBannerTitle")).toBeInTheDocument();
    // still no select
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});

// spec 2026-11-09-clarte-2 — R1 2 groupes + compteurs, R2 Équipe dans le ⋯, R3 plus de blocage
describe("project layout (spec clarte-2)", () => {
  const fullOverview = (over: Partial<ProjectOverview> = {}): ProjectOverview => ({
    articles: 10,
    fulltext: 10,
    analysed: 5,
    reviewed: 5,
    gold: 2,
    searches: [mkSearch({ last_benchmark: { id: "b1", status: "COMPLETED", created_at: null } })],
    ...over,
  });

  it("R1: two groups with a visible legend, no italic, aria-current on the active tab, counters shown", async () => {
    mockState.pathname = "/projects/p1";
    getOverview.mockResolvedValue(fullOverview());
    listWatches.mockResolvedValue([
      { status: "ACTIVE" },
      { status: "PAUSED" },
    ] as unknown as Watch[]);
    const { container } = mount();
    // the lead-only Import tab appears once the project loads
    await screen.findByRole("link", { name: /import/ });
    const nav = container.querySelector('nav[aria-label="steps"]');
    expect(nav).not.toBeNull();
    // no italic anywhere (the « optionnel » is no longer carried by a font style)
    expect(nav?.querySelector(".italic")).toBeNull();
    // the group legend is visible text, not a tooltip
    expect(nav?.textContent).toContain("measureLegend");
    expect(nav?.querySelector("[title]")).toBeNull();
    // lead: 4 core tabs + 3 measure tabs
    expect(nav?.querySelectorAll("a")).toHaveLength(7);
    // the active tab is current
    expect(screen.getByRole("link", { name: /overview/ })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: /gold/ })).not.toHaveAttribute("aria-current");
    // counters: 2 gold refs, 1 benchmark draw, 1 active watch (watches arrive after the lead flag settles)
    await waitFor(() => expect(nav?.textContent).toContain('counterRefs({"n":2})'));
    expect(nav?.textContent).toContain('counterDraws({"n":1})');
    await waitFor(() => expect(nav?.textContent).toContain('counterActive({"n":1})'));
  });

  it("R1: the counters are masked at 0", async () => {
    mockState.pathname = "/projects/p1";
    getOverview.mockResolvedValue(fullOverview({ gold: 0, searches: [] }));
    listWatches.mockResolvedValue([]);
    const { container } = mount();
    await screen.findByRole("link", { name: /import/ });
    const nav = container.querySelector('nav[aria-label="steps"]');
    expect(nav?.textContent).not.toContain("counterRefs");
    expect(nav?.textContent).not.toContain("counterDraws");
    expect(nav?.textContent).not.toContain("counterActive");
  });

  it("R2: team is out of the row and in the ⋯ menu for a member (no destructive action)", async () => {
    mockState.pathname = "/projects/p1";
    getOverview.mockResolvedValue(fullOverview());
    listWatches.mockResolvedValue([]);
    const { container } = mount({ role_in_project: "ANNOTATOR" });
    await screen.findByRole("link", { name: /overview/ });
    const nav = container.querySelector('nav[aria-label="steps"]');
    // member: 3 core tabs (import hidden) + gold only in the measure group
    expect(nav?.querySelectorAll("a")).toHaveLength(4);
    expect(nav?.querySelector('a[href*="/team"]')).toBeNull();
    // the ⋯ menu is visible to the member and carries Équipe, not the delete
    fireEvent.click(await screen.findByRole("button", { name: "menuActions" }));
    const team = await screen.findByRole("menuitem", { name: /team/ });
    expect(team).toHaveAttribute("href", "/projects/p1/team");
    expect(screen.queryByText("deleteProject")).not.toBeInTheDocument();
  });

  it("R2: the lead menu carries Équipe then the destructive action", async () => {
    mockState.pathname = "/projects/p1";
    getOverview.mockResolvedValue(fullOverview());
    listWatches.mockResolvedValue([]);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "menuActions" }));
    expect(await screen.findByRole("menuitem", { name: /team/ })).toBeInTheDocument();
    expect(screen.getByText("deleteProject")).toBeInTheDocument();
  });

  it("R3: no blocked tab — every step is a navigable link on a corpus-less project", async () => {
    mockState.pathname = "/projects/p1/inference";
    getOverview.mockResolvedValue(fullOverview({ articles: 0, gold: 0, searches: [] }));
    listWatches.mockResolvedValue([]);
    const { container } = mount({ status: "FROZEN", article_count: 0, role_in_project: "ANNOTATOR" });
    await screen.findByRole("link", { name: /overview/ });
    const nav = container.querySelector('nav[aria-label="steps"]');
    // member: overview + inference + articles + gold — all links, none greyed out
    expect(nav?.querySelectorAll("a")).toHaveLength(4);
    expect(nav?.querySelector(".opacity-50")).toBeNull();
    expect(nav?.querySelector(".cursor-not-allowed")).toBeNull();
    expect(screen.getByRole("link", { name: /inference/ })).toHaveAttribute("aria-current", "page");
  });
});
