import { Suspense } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import * as navModule from "next/navigation";
import InferencePage from "@/app/projects/[id]/inference/page";
import * as api from "@/lib/api";
import { SearchInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";

// i18n keys returned as-is (stable identity: page effects depend on the t callback):
// the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => {
  const replace = jest.fn();
  // The current `?search=` value of the mocked location (set by the tests).
  const state = { searchParam: null as string | null };
  return {
    replace,
    state,
    useParams: () => ({ id: "p1" }),
    useSearchParams: () => ({
      get: (k: string) => (k === "search" ? state.searchParam : null),
      toString: () => (state.searchParam ? `search=${state.searchParam}` : ""),
    }),
    usePathname: () => "/projects/p1/inference",
    useRouter: () => ({ replace }),
  };
});
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
// Concordance panels (spec 2026-11-08): moved to the benchmarks page — these
// markers let the page test assert they no longer render here.
jest.mock("@/components/ComparisonPanel", () => ({
  ComparisonPanel: () => <div data-testid="marker-comparison" />,
}));
jest.mock("@/components/AgreementPanel", () => ({
  AgreementPanel: () => <div data-testid="marker-agreement" />,
}));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listSearches: jest.fn(),
  getInference: jest.fn(),
  getProject: jest.fn(),
  listModels: jest.fn(),
  listGold: jest.fn(),
  getPrompt: jest.fn(),
  deleteSearch: jest.fn(),
  previewSelection: jest.fn(),
  createSearch: jest.fn(),
  triggerInference: jest.fn(),
  criteriaCount: jest.fn(),
  criteriaArticles: jest.fn(),
  getResultsIndex: jest.fn(),
  triggerResultsIndex: jest.fn(),
}));

const mockUseAuth = useAuth as unknown as jest.Mock;
const nav = navModule as unknown as {
  replace: jest.Mock;
  state: { searchParam: string | null };
};
/** getInference that 404s for a (deleted) search id, and resolves null for the default. */
const inference404Stale = (_p: string, s?: string, all?: boolean): Promise<unknown> =>
  all
    ? Promise.resolve(null)
    : s
      ? Promise.reject(new api.ApiError(404, "search not found"))
      : Promise.resolve(null);
const listSearches = api.listSearches as jest.Mock;
const getInference = api.getInference as jest.Mock;
const getProject = api.getProject as jest.Mock;
const listModels = api.listModels as jest.Mock;
const listGold = api.listGold as jest.Mock;
const deleteSearch = api.deleteSearch as jest.Mock;

const search: SearchInfo = {
  id: "s1",
  name: "S1",
  kind: "standard",
  is_default: true,
  question: "q?",
  fields: [{ key: "ai", label: "AI", type: "boolean" }],
  text_sections: null,
  parent: null,
  parent_spec: null,
  created_at: null,
  created_by: null,
  lot_size: 10,
  predicted: 0,
  reviewed: 0,
  breakdown: {},
  to_review: 0,
  last_job: null,
  last_benchmark: null,
};

const model = { id: "m1", name: "Kev" } as unknown as api.ModelInfo;
const runningJob = {
  id: "j1",
  project_id: "p1",
  search_id: "s1",
  status: "RUNNING",
  result: { done: 1, total: 10 },
} as unknown as api.InferenceJob;

const completedJob = {
  id: "j2",
  project_id: "p1",
  search_id: "s1",
  status: "COMPLETED",
  result: { done: 10, total: 10, auto_accepted: 6, auto_rejected: 1, needs_review: 3 },
  model_used: "Kev",
} as unknown as api.InferenceJob;

function mount(overrides: {
  user: { role?: string };
  roleInProject: string | null;
  busy?: api.InferenceJob | null;
  job?: api.InferenceJob | null;
  searches?: SearchInfo[];
  question?: string | null;
  fields?: api.SchemaField[];
  /** count of the selection preview (default 4) */
  preview?: number;
  /** corpus size of the mocked project (default 10) */
  articleCount?: number;
  /** Overrides getInference (e.g. to 404 for a stale selection); default resolves. */
  inference?: (_p: string, s?: string, all?: boolean) => Promise<unknown>;
}) {
  mockUseAuth.mockReturnValue({ user: overrides.user, loading: false });
  listSearches.mockResolvedValue(overrides.searches ?? [search]);
  getProject.mockResolvedValue({
    role_in_project: overrides.roleInProject,
    question_set: true,
    question: overrides.question ?? "q?",
    fields: overrides.fields ?? search.fields,
    text_sections: ["methods"],
    article_count: overrides.articleCount ?? 10,
  });
  listModels.mockResolvedValue([model]);
  listGold.mockResolvedValue([]);
  (api.previewSelection as jest.Mock).mockResolvedValue(overrides.preview ?? 4);
  getInference.mockImplementation(
    overrides.inference ??
      ((_p: string, _s?: string, all?: boolean) =>
        Promise.resolve(all ? (overrides.busy ?? null) : (overrides.job ?? null))),
  );
  return render(
    <Suspense fallback={null}>
      <InferencePage />
    </Suspense>
  );
}

async function panelOpen() {
  // the creation panel opens once the searches, the project role and the models are loaded
  await screen.findByText("panelTitle");
  await waitFor(() => expect(listModels).toHaveBeenCalled());
}

beforeEach(() => {
  nav.replace.mockClear();
  nav.state.searchParam = null;
});

describe("inference page — chaining (spec 2026-11-02-legacy-questions §4)", () => {
  it("the chain box is NOT offered while no analysis runs (predicted < lot_size is not enough)", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", busy: null });
    await panelOpen();
    expect(screen.queryByText("chainLabel")).not.toBeInTheDocument();
  });

  it("the chain box is offered while an analysis runs", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", busy: runningJob });
    await panelOpen();
    expect(screen.getByText("chainLabel")).toBeInTheDocument();
  });
});

describe("inference page — delete a search (spec §5 + §6)", () => {
  beforeEach(() => {
    window.confirm = jest.fn(() => true);
  });

  // a touched search (predicted > 0) is shown in the list → its AnalysisRow
  const touched = { ...search, predicted: 2 };

  it("the delete button is visible for a lead, on the initial search too", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [touched] });
    await panelOpen();
    await screen.findByText("delete");
  });

  it("the delete button is visible for a global admin (not a member)", async () => {
    mount({ user: { role: "ADMIN" }, roleInProject: null, searches: [touched] });
    await panelOpen();
    await screen.findByText("delete");
  });

  it("no delete button for a non-lead member", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "ANNOTATOR", searches: [touched] });
    await screen.findByText("leadOnly"); // no creation panel for a non-lead
    expect(screen.queryByText("delete")).not.toBeInTheDocument();
  });

  it("confirm → deleteSearch is called and the list is refreshed", async () => {
    deleteSearch.mockResolvedValue(undefined);
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [touched] });
    await panelOpen();
    fireEvent.click(await screen.findByText("delete"));
    await waitFor(() => expect(deleteSearch).toHaveBeenCalledWith("p1", "s1"));
    await waitFor(() => expect(listSearches.mock.calls.length).toBeGreaterThanOrEqual(2)); // refresh
  });

  it("403: the error is shown, the row stays", async () => {
    deleteSearch.mockRejectedValue(new api.ApiError(403, "lead required"));
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [touched] });
    await panelOpen();
    fireEvent.click(await screen.findByText("delete"));
    await screen.findByText("errors.leadRequired"); // the existing error channel
    expect(screen.getByText("S1")).toBeInTheDocument(); // the row is intact
  });

  it("stored search deleted: the polling 404 falls back cleanly (spec §6)", async () => {
    sessionStorage.setItem("diggy:search:p1", "deleted-id");
    // a visible (touched) row stays in the list, pointing at a stored search that no longer exists
    mount({
      user: { role: "RESEARCHER" },
      roleInProject: "LEAD",
      searches: [{ ...search, predicted: 1 }],
      inference: inference404Stale,
    });
    await panelOpen();
    // no crash, no job shown for the dead search
    expect(screen.queryByText("progressTitle")).not.toBeInTheDocument();
    expect(screen.getByText("S1")).toBeInTheDocument();
    // and no error banner: the stale selection quietly falls back to the default search
    await waitFor(() => expect(screen.queryByText("errors.notFound")).not.toBeInTheDocument(), {
      timeout: 2000,
    });
    sessionStorage.removeItem("diggy:search:p1");
  });
});

// A selection pointing at a deleted analysis (tab memory or `?search=` in the URL):
// the pages must fall back to the default search quietly, without a "not found" banner.
describe("inference page — stale selection after a search was deleted", () => {
  const stale = "deleted-id";

  beforeEach(() => {
    nav.replace.mockClear();
    nav.state.searchParam = null;
    sessionStorage.removeItem("diggy:search:p1");
  });

  it("stored stale search: no banner, the stored id is dropped, default job shown", async () => {
    sessionStorage.setItem("diggy:search:p1", stale);
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [], inference: inference404Stale });
    await panelOpen();
    // the dead selection converges to the default search: no "not found" banner…
    await waitFor(() => expect(screen.queryByText("errors.notFound")).not.toBeInTheDocument(), {
      timeout: 2000,
    });
    // …and the stale reference is purged so the next fetches use the default search.
    await waitFor(() => expect(sessionStorage.getItem("diggy:search:p1")).toBeNull(), {
      timeout: 2000,
    });
    expect(getInference).toHaveBeenCalledWith("p1", undefined); // the default search
  });

  it("URL stale search: the `?search=` param is rewritten away", async () => {
    nav.state.searchParam = stale;
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [], inference: inference404Stale });
    await panelOpen();
    await waitFor(() => expect(screen.queryByText("errors.notFound")).not.toBeInTheDocument(), {
      timeout: 2000,
    });
    // the URL is rewritten without the dead `?search=` (other params are preserved)
    await waitFor(() => {
      const urls = nav.replace.mock.calls.map((c) => c[0]);
      expect(urls).toContain("/projects/p1/inference");
    }, { timeout: 2000 });
    expect(screen.queryByText("errors.notFound")).not.toBeInTheDocument();
  });
});

// spec 2026-11-02-frontend-vocabulary-a11y-polish §3, §4, §5, §8
describe("inference page — « à lancer » section, aria-live, polish", () => {
  const touched = { ...search, predicted: 2 };

  it("untouched analysis: the « à lancer » section renders it; « analyses réalisées » hidden", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD" });
    await panelOpen();
    expect(screen.getByText("toLaunch")).toBeInTheDocument();
    expect(screen.getByText("badgeToLaunch")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "launch" })).toBeInTheDocument();
    expect(screen.queryByText("listTitle")).not.toBeInTheDocument();
  });

  it("only launched analyses: the list is shown, no « à lancer » section", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [touched] });
    await panelOpen();
    expect(screen.getByText("listTitle")).toBeInTheDocument();
    expect(screen.queryByText("toLaunch")).not.toBeInTheDocument();
    expect(screen.queryByText("badgeToLaunch")).not.toBeInTheDocument();
  });

  it("empty state NOT shown while an untouched analysis exists (non-lead)", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "ANNOTATOR" });
    await screen.findByText("leadOnly");
    expect(screen.getByText("toLaunch")).toBeInTheDocument();
    expect(screen.queryByText("emptyHint")).not.toBeInTheDocument();
  });

  it("empty state shown when no analysis exists at all (non-lead)", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "ANNOTATOR", searches: [] });
    await screen.findByText("leadOnly");
    expect(screen.getByText("emptyHint")).toBeInTheDocument();
    expect(screen.queryByText("toLaunch")).not.toBeInTheDocument();
  });

  it("collapsed panel button: « + Créer » (panelCreate)", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [touched] });
    await panelOpen();
    expect(screen.getByText(/\+ panelCreate/)).toBeInTheDocument();
  });

  it("completed job: the CTA banner offers the review action", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [touched], job: completedJob });
    await panelOpen();
    // the CTA banner (spec 2026-11-09 R3) replaced the old button row; the
    // « aller à la relecture » action is now its ctaReview item (needs_review=3).
    expect(await screen.findByText(/ctaReview/)).toBeInTheDocument();
  });

  it("the avancement card wrapper carries aria-live=polite", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [touched], job: completedJob });
    await panelOpen();
    const card = await screen.findByText("progressTitle");
    expect(card.closest("[aria-live]")).toHaveAttribute("aria-live", "polite");
  });
});

// The « Enregistrer » button: create the analysis without launching it, so the question
// and the fields can be completed later (the analysis then shows in « à lancer »).
describe("inference page — « Enregistrer » (save without launching)", () => {
  const saved = { ...search, id: "s2", is_default: false, name: "261103-1" };
  const createSearch = api.createSearch as jest.Mock;
  const triggerInference = api.triggerInference as jest.Mock;

  beforeEach(() => {
    createSearch.mockClear();
    triggerInference.mockClear();
  });

  it("creates the analysis without launching it; the panel stays open", async () => {
    createSearch.mockResolvedValue(saved);
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD" });
    await panelOpen();
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(createSearch).toHaveBeenCalledWith(
        "p1",
        expect.objectContaining({ kind: "standard", question: "q?", selection: {} })
      )
    );
    expect(triggerInference).not.toHaveBeenCalled();
    // the new analysis becomes the current one (?search= in the URL)
    await waitFor(() =>
      expect(nav.replace.mock.calls.map((c) => c[0]).some((u) => u?.includes("search=s2")))
    );
    // the panel stays open so the question can be completed before the launch
    expect(screen.getByText("panelTitle")).toBeInTheDocument();
  });

  it("save with an empty question+fields: no 422 (fields left unset)", async () => {
    createSearch.mockResolvedValue(saved);
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", question: "", fields: [] });
    await panelOpen();
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() => expect(createSearch).toHaveBeenCalled());
    const payload = createSearch.mock.calls[createSearch.mock.calls.length - 1][1];
    expect(payload.question).toBe("");
    expect(payload.fields).toBeUndefined();
    expect(triggerInference).not.toHaveBeenCalled();
  });

  it("save is disabled when no article is selected", async () => {
    createSearch.mockResolvedValue(saved);
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", preview: 0 });
    await panelOpen();
    // the live preview is debounced (400 ms): wait for it to report a count of 0
    await waitFor(
      () => expect(screen.getByRole("button", { name: "save" })).toBeDisabled(),
      { timeout: 2000 }
    );
    expect(createSearch).not.toHaveBeenCalled();
  });

  it("an analysis created but not launched shows in « à lancer » with its « Lancer » button", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [search, saved] });
    expect(await screen.findByText("toLaunch")).toBeInTheDocument();
    const launches = screen.getAllByRole("button", { name: "launch" });
    expect(launches).toHaveLength(2); // the default one + the saved one
  });
});

describe("inference page — creation panel polish (UI feedback 2026-11-03)", () => {
  it("« partir des articles de » is hidden while no analysis has been launched", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD" }); // default search, untouched
    await panelOpen();
    expect(screen.queryByText("selFrom")).not.toBeInTheDocument();
  });

  it("« partir des articles de » is offered once an analysis has been launched", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [{ ...search, predicted: 2 }] });
    await panelOpen();
    // the panel stays collapsed on a project with launched analyses: open it
    fireEvent.click(screen.getByText(/\+ panelCreate/));
    expect(await screen.findByText("selFrom")).toBeInTheDocument();
  });

  it("a single article count is shown (the duplicate « seront analysés » line is gone)", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD" });
    await panelOpen();
    // the live preview is debounced (400 ms): wait for the selector's count to settle
    await waitFor(() => expect(screen.getByText("selected")).toBeInTheDocument(), { timeout: 2000 });
    expect(screen.queryByText("scopeCount")).not.toBeInTheDocument();
  });
});

describe("inference page — concordance moved to benchmarks (2026-11-08)", () => {
  it("no longer renders the comparison / agreement panels", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD" });
    await panelOpen();
    expect(screen.queryByTestId("marker-comparison")).not.toBeInTheDocument();
    expect(screen.queryByTestId("marker-agreement")).not.toBeInTheDocument();
  });

  it("chat scope selector offers the non-default standard searches only", async () => {
    // The default search's lot is the whole corpus: offering it as a scope
    // would duplicate the "whole corpus" option (audit finding
    // lotall-scope-duplicates-corpus).
    const saved = { ...search, id: "s2", is_default: false, name: "261103-1" };
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [search, saved] });
    await panelOpen();
    const select = await screen.findByTestId("chat-scope");
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.value);
    // "corpus" is the panel's whole-corpus option; s1 (default, lot_all) is
    // out — its scope is the corpus itself.
    expect(options).toEqual(["corpus", "s2"]);
  });
});

// spec 2026-11-09-sous-analyse-criteres R2/R3: the criteria block in the
// creation form + the CTA banner at the end of a completed analysis.
describe("inference page — criteria block + CTA banner (spec 2026-11-09)", () => {
  const criteriaCount = api.criteriaCount as jest.Mock;
  const getResultsIndex = api.getResultsIndex as jest.Mock;
  const analysed = { ...search, id: "sB", is_default: false, name: "B", predicted: 5, lot_size: 10 };

  beforeEach(() => {
    // the CTA « chatter » button scrolls the chat panel into view (jsdom: not implemented)
    (window.HTMLElement.prototype as unknown as { scrollIntoView: jest.Mock }).scrollIntoView = jest.fn();
  });

  it("the criteria block appears only when the source analysis has been analysed", async () => {
    criteriaCount.mockResolvedValue(3);
    // default untouched search + an analysed one: the panel is collapsed (a search launched)
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [search, analysed] });
    await panelOpen();
    fireEvent.click(screen.getByText(/\+ panelCreate/));
    await screen.findByText("selFrom");
    expect(screen.queryByTestId("criteria-block")).not.toBeInTheDocument();
    // pick the analysed search B as the source → the criteria block shows
    const fromSel = screen.getByText("selFrom").closest("label")!.querySelector("select")!;
    fireEvent.change(fromSel, { target: { value: "sB" } });
    await screen.findByTestId("criteria-block");
    // the live counter settles on the server's number
    await waitFor(() => expect(screen.getByTestId("crit-count")).toHaveTextContent("critCount"));
  });

  it("the CTA banner is offered at the end of a completed analysis", async () => {
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [{ ...search, predicted: 2 }], job: completedJob });
    await panelOpen();
    expect(await screen.findByTestId("cta-banner")).toBeInTheDocument();
  });

  it("ctaChat switches the chat panel to the results mode of the analysis", async () => {
    getResultsIndex.mockResolvedValue({ status: "ready", done: 10, total: 10 });
    mount({ user: { role: "RESEARCHER" }, roleInProject: "LEAD", searches: [{ ...search, predicted: 2 }], job: completedJob });
    await panelOpen();
    await screen.findByTestId("cta-banner");
    // initially the panel is in corpus mode
    expect(screen.getByTestId("chat-mode-corpus")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "ctaChat" }));
    await waitFor(() => expect(screen.getByTestId("chat-mode-results")).toHaveAttribute("aria-pressed", "true"));
  });
});

// spec 2026-11-09-clarte-2 — R3: on a corpus-less project the empty state offers the Import CTA
describe("inference page — spec clarte-2 R3: empty corpus CTA", () => {
  it("the empty state offers « Import » to a lead on a corpus-less project", async () => {
    mount({ user: {}, roleInProject: "LEAD", searches: [], articleCount: 0 });
    expect(await screen.findByText("importCta")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "importCta" })).toHaveAttribute("href", "/projects/p1/import");
  });

  it("no CTA when the corpus is not empty", async () => {
    mount({ user: {}, roleInProject: "LEAD", searches: [], articleCount: 10 });
    await panelOpen();
    expect(screen.queryByText("importCta")).not.toBeInTheDocument();
  });
});
