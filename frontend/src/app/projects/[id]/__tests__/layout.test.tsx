import { act, render, screen, waitFor } from "@testing-library/react";
import ProjectLayout from "@/app/projects/[id]/layout";
import * as api from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { SearchInfo } from "@/lib/api";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
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
}));

const mockUseAuth = useAuth as unknown as jest.Mock;
const getProject = api.getProject as jest.Mock;
const getInference = api.getInference as jest.Mock;
const listJobs = api.listJobs as jest.Mock;
const listSearches = api.listSearches as jest.Mock;

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

function mount() {
  mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
  getProject.mockResolvedValue(project);
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
