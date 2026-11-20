import { Suspense } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import * as navModule from "next/navigation";
import ArticlesPage from "@/app/projects/[id]/articles/page";
import * as api from "@/lib/api";
import { Article, InferenceJob, SearchInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useSearchId, useSearches } from "@/lib/useSearch";

// i18n keys returned as-is (stable identity: page effects depend on the t callback):
// the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => {
  const replace = jest.fn();
  // The current URL params of the mocked location (set by the tests).
  const state = { searchParam: "s1" as string | null, runParam: null as string | null };
  return {
    replace,
    state,
    useParams: () => ({ id: "p1" }),
    usePathname: () => "/projects/p1/articles",
    useSearchParams: () => ({
      get: (k: string) => (k === "search" ? state.searchParam : k === "run" ? state.runParam : null),
      toString: () => {
        const p = new URLSearchParams();
        if (state.searchParam) p.set("search", state.searchParam);
        if (state.runParam) p.set("run", state.runParam);
        return p.toString();
      },
    }),
    useRouter: () => ({ push: jest.fn(), replace }),
  };
});
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/useSearch", () => ({ useSearchId: jest.fn(), useSearches: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getProject: jest.fn(),
  listArticles: jest.fn(),
  deleteArticle: jest.fn(),
  bulkDeleteArticles: jest.fn(),
  listSearchJobs: jest.fn(),
  getOverview: jest.fn(),
  listProjects: jest.fn(),
  prismaUrl: jest.fn(() => "/prisma.svg"),
  prismaPdfUrl: jest.fn(() => "/prisma.pdf"),
}));
// the search/export side-panels are out of scope of this test
jest.mock("@/components/ExportMenu", () => ({ ExportMenu: () => null }));
jest.mock("@/components/SemanticSearchBox", () => ({ SemanticSearchBox: () => null }));

const mockUseAuth = useAuth as unknown as jest.Mock;
const useSearchModule = { useSearchId, useSearches } as unknown as {
  useSearchId: jest.Mock;
  useSearches: jest.Mock;
};
const nav = navModule as unknown as {
  replace: jest.Mock;
  state: { searchParam: string | null; runParam: string | null };
};
const getProject = api.getProject as jest.Mock;
const listArticles = api.listArticles as jest.Mock;
const listSearchJobs = api.listSearchJobs as jest.Mock;
const getOverview = api.getOverview as jest.Mock;
const listProjects = api.listProjects as jest.Mock;

const corpus: SearchInfo = {
  id: "s1",
  name: "S1",
  kind: "standard",
  is_default: true,
  lot_size: 10,
  predicted: 5,
  last_job: { status: "COMPLETED", model: "Kev" },
} as unknown as SearchInfo;

// two runs of the corpus, newest first (the backend order)
const newest: InferenceJob = {
  id: "j2",
  project_id: "p1",
  model_used: "Kev",
  search_id: "s1",
  status: "COMPLETED",
  name: "261120-2",
  n_predictions: 10,
  result: { done: 10, total: 10 },
  error: null,
  created_at: "2026-11-20T08:00:00Z",
} as unknown as InferenceJob;
const older: InferenceJob = {
  ...newest,
  id: "j1",
  name: "261101-1",
  created_at: "2026-11-01T09:00:00Z",
} as unknown as InferenceJob;

const ARTICLE: Article = {
  id: "a1",
  title: "Titre 1",
  doi: "10.1/x",
  source: null,
  native_id: null,
  has_fulltext: true,
  fulltext_source: "upload",
  origin: "upload",
  origin_seeds: 0,
  prediction_status: "AUTO_ACCEPTED",
  confidence: 0.9,
  annotated: false,
  review: null,
  answers: null,
} as unknown as Article;

function mount(overrides: {
  role?: "LEAD" | "ANNOTATOR";
  corpora?: SearchInfo[];
  jobs?: InferenceJob[];
  articleCount?: number;
}) {
  const role = overrides.role ?? "LEAD";
  const corpora = overrides.corpora ?? [corpus];
  mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
  useSearchModule.useSearchId.mockReturnValue("s1");
  useSearchModule.useSearches.mockReturnValue({
    searches: corpora,
    current: corpora.find((s) => s.is_default) ?? corpora[0],
    reload: jest.fn(),
  });
  getProject.mockResolvedValue({
    role_in_project: role,
    question_set: true,
    question: "q?",
    fields: [],
    text_sections: [],
    triage_policy: null,
    article_count: overrides.articleCount ?? 10,
  });
  listSearchJobs.mockResolvedValue({ jobs: overrides.jobs ?? [] });
  listArticles.mockResolvedValue({ items: [ARTICLE], total: 1, page: 1, per_page: 100, stats: {} });
  getOverview.mockResolvedValue({
    articles: overrides.articleCount ?? 10,
    fulltext: 0,
    analysed: 0,
    reviewed: 0,
    gold: 0,
    searches: [],
  });
  listProjects.mockResolvedValue([{ id: "p1", role_in_project: role }]);
  return render(
    <Suspense fallback={null}>
      <ArticlesPage />
    </Suspense>
  );
}

beforeEach(() => {
  nav.replace.mockClear();
  nav.state.searchParam = "s1";
  nav.state.runParam = null;
  listArticles.mockClear();
});

const lastRunArg = (): unknown => {
  const call = listArticles.mock.calls[listArticles.mock.calls.length - 1];
  return call[9]; // the `run` parameter (10th)
};

// spec 2026-11-09-clarte-2 — R3: empty corpus → the empty state offers the Import CTA.
// spec 2026-11-20-analyses-runs-2 R2: a corpus-less project never calls search=null APIs.
describe("articles page — no corpus (R2/S1)", () => {
  it("no corpus, no articles → the « import » CTA stays for a lead", async () => {
    mount({ corpora: [], articleCount: 0 });
    expect(await screen.findByText("noCorpus")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "importCta" })).toHaveAttribute("href", "/projects/p1/import");
    // the search-scoped list would 404 on search=null: it is never called
    expect(listArticles).not.toHaveBeenCalled();
  });

  it("no corpus, no articles → no CTA for a member", async () => {
    mount({ role: "ANNOTATOR", corpora: [], articleCount: 0 });
    expect(await screen.findByText("noCorpus")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "importCta" })).not.toBeInTheDocument();
  });

  // spec 2026-11-20-corpus-view R2: articles without any analysis → the corpus view
  // (the old dead-end CorporaEmpty only remains for a truly empty project, above).
  it("no corpus, articles waiting → the corpus view, fetched without search, with the analysis CTA", async () => {
    mount({ corpora: [], articleCount: 12 });
    expect(await screen.findByText("corpusTitle")).toBeInTheDocument();
    await waitFor(() => expect(listArticles).toHaveBeenCalled());
    expect(listArticles.mock.calls[0][6]).toBeUndefined(); // no search id
    expect(screen.getByRole("link", { name: /corpusCtaAction/ })).toHaveAttribute(
      "href",
      "/projects/p1/inference"
    );
    expect(getProject).not.toHaveBeenCalled();
  });

  it("no corpus, articles waiting → no CTA for a member", async () => {
    mount({ role: "ANNOTATOR", corpora: [], articleCount: 12 });
    expect(await screen.findByText("corpusTitle")).toBeInTheDocument();
    expect(screen.queryByText("corpusCta")).not.toBeInTheDocument();
  });
});

// spec 2026-11-20-analyses-runs-2 F5 — run selector (R5/R9).
describe("articles page — spec 2026-11-20-2 F5 (run selector)", () => {
  it("2 runs → the selector defaults to the newest run; single fetch with that run; no ?run= in the URL", async () => {
    mount({ corpora: [corpus], jobs: [newest, older] });
    await screen.findByText("Titre 1");
    const selector = screen.getByRole("combobox", { name: "runSelector" });
    expect(selector).toHaveValue("j2");
    expect(screen.getByText("runSelectorNote")).toBeInTheDocument();
    // the first fetch reads the newest run's results
    expect(listArticles).toHaveBeenCalledTimes(1);
    expect(lastRunArg()).toBe("j2");
    // the default run is NOT persisted in the URL (R9)
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("picking an older run → fetch with run=<jid> and the URL gets ?run=", async () => {
    mount({ corpora: [corpus], jobs: [newest, older] });
    await screen.findByText("Titre 1");
    const selector = screen.getByRole("combobox", { name: "runSelector" });
    fireEvent.change(selector, { target: { value: "j1" } });
    await waitFor(() => expect(listArticles).toHaveBeenCalledTimes(2));
    expect(lastRunArg()).toBe("j1");
    await waitFor(() =>
      expect(nav.replace.mock.calls.map((c) => c[0]).some((u) => u?.includes("run=j1") && u?.includes("search=s1")))
    );
  });

  it("0 runs → no selector, the fetch keeps the search's predictions (no run)", async () => {
    mount({ corpora: [corpus], jobs: [] });
    await screen.findByText("Titre 1");
    expect(screen.queryByRole("combobox", { name: "runSelector" })).not.toBeInTheDocument();
    expect(listArticles).toHaveBeenCalledTimes(1);
    expect(lastRunArg()).toBeUndefined();
  });
});
