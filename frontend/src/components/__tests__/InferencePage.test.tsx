import { Suspense } from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import * as navModule from "next/navigation";
import InferencePage from "@/app/projects/[id]/inference/page";
import * as api from "@/lib/api";
import { searchStoreKey } from "@/lib/search";
import { InferenceJob, ModelInfo, SchemaField, SearchInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";

// i18n keys returned as-is (stable identity: page effects depend on the t callback):
// the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => {
  const replace = jest.fn();
  // The current URL params of the mocked location (set by the tests).
  const state = { searchParam: null as string | null, newParam: null as string | null };
  return {
    replace,
    state,
    useParams: () => ({ id: "p1" }),
    useSearchParams: () => ({
      get: (k: string) => (k === "search" ? state.searchParam : k === "new" ? state.newParam : null),
      toString: () => {
        const p = new URLSearchParams();
        if (state.searchParam) p.set("search", state.searchParam);
        if (state.newParam) p.set("new", state.newParam);
        return p.toString();
      },
    }),
    usePathname: () => "/projects/p1/inference",
    useRouter: () => ({ replace }),
  };
});
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listSearches: jest.fn(),
  getInference: jest.fn(),
  getProject: jest.fn(),
  getOverview: jest.fn(),
  listProjects: jest.fn(),
  listModels: jest.fn(),
  listGold: jest.fn(),
  listSearchJobs: jest.fn(),
  deleteSearchJob: jest.fn(),
  previewSelection: jest.fn(),
  createSearch: jest.fn(),
  updateProject: jest.fn(),
  triggerInference: jest.fn(),
  rerunSearch: jest.fn(),
  cancelInference: jest.fn(),
}));

const mockUseAuth = useAuth as unknown as jest.Mock;
const nav = navModule as unknown as {
  replace: jest.Mock;
  state: { searchParam: string | null; newParam: string | null };
};
const listSearches = api.listSearches as jest.Mock;
const getInference = api.getInference as jest.Mock;
const getProject = api.getProject as jest.Mock;
const getOverview = api.getOverview as jest.Mock;
const listProjects = api.listProjects as jest.Mock;
const listModels = api.listModels as jest.Mock;
const listGold = api.listGold as jest.Mock;
const listSearchJobs = api.listSearchJobs as jest.Mock;
const deleteSearchJob = api.deleteSearchJob as jest.Mock;
const createSearch = api.createSearch as jest.Mock;
const updateProject = api.updateProject as jest.Mock;
const triggerInference = api.triggerInference as jest.Mock;
const rerunSearch = api.rerunSearch as jest.Mock;
const cancelInference = api.cancelInference as jest.Mock;

const search: SearchInfo = {
  id: "s1",
  name: "S1",
  kind: "standard",
  is_default: true,
  question: "q?",
  fields: [{ key: "ai", label: "AI", type: "boolean" }],
  text_sections: ["methods"],
  parent: null,
  parent_spec: null,
  created_at: null,
  created_by: null,
  lot_size: 10,
  predicted: 5,
  reviewed: 0,
  breakdown: {},
  to_review: 0,
  last_job: { status: "COMPLETED", model: "Kev" },
  last_benchmark: null,
};
const draftSearch: SearchInfo = { ...search, last_job: null, predicted: 0 };

const models: ModelInfo[] = [
  { id: "m1", name: "Kev", ram_usage_mb: 4000 },
  { id: "m2", name: "other", ram_usage_mb: 8000 },
] as unknown as ModelInfo[];

const completedJob = {
  id: "j1",
  project_id: "p1",
  prompt_template_id: null,
  model_used: "Kev",
  model_info: { main: { name: "Kev", backend: "ollama", identifier: "Kev", version: "v1" }, prefilter: null },
  search_id: "s1",
  status: "COMPLETED",
  name: "261101-1",
  n_predictions: 10,
  breakdown: { AUTO_ACCEPTED: 6, AUTO_REJECTED: 1, NEEDS_HUMAN_REVIEW: 3 },
  result: { done: 10, total: 10, auto_accepted: 6, auto_rejected: 1, needs_review: 3 },
  error: null,
  created_at: "2026-11-01T09:00:00Z",
  completed_at: "2026-11-01T09:30:00Z",
} as unknown as InferenceJob;
const runningJob = {
  ...completedJob,
  id: "j2",
  status: "RUNNING",
  name: "261120-2",
  n_predictions: 4,
  breakdown: {},
  result: { done: 4, total: 10 },
  created_at: "2026-11-20T08:00:00Z",
  completed_at: null,
} as unknown as InferenceJob;

function mount(overrides: {
  user: { role?: string };
  roleInProject?: string | null;
  searches?: SearchInfo[];
  jobs?: InferenceJob[];
  busy?: InferenceJob | null;
  articleCount?: number;
  question?: string | null;
  fields?: SchemaField[];
  /** count of the selection preview (default 4) */
  preview?: number;
}) {
  mockUseAuth.mockReturnValue({ user: overrides.user, loading: false });
  listSearches.mockResolvedValue(overrides.searches ?? [search]);
  getOverview.mockResolvedValue({
    articles: overrides.articleCount ?? 10,
    fulltext: 0,
    analysed: 0,
    reviewed: 0,
    gold: 0,
    searches: [],
  });
  listProjects.mockResolvedValue([{ id: "p1", role_in_project: overrides.roleInProject ?? "LEAD" }]);
  getProject.mockResolvedValue({
    role_in_project: overrides.roleInProject ?? "LEAD",
    question_set: true,
    question: overrides.question ?? "q?",
    fields: overrides.fields ?? search.fields,
    text_sections: ["methods"],
    article_count: overrides.articleCount ?? 10,
  });
  listModels.mockResolvedValue(models);
  listGold.mockResolvedValue([]);
  listSearchJobs.mockResolvedValue({ jobs: overrides.jobs ?? [] });
  getInference.mockResolvedValue(overrides.busy ?? null);
  (api.previewSelection as jest.Mock).mockResolvedValue(overrides.preview ?? 4);
  return render(
    <Suspense fallback={null}>
      <InferencePage />
    </Suspense>
  );
}

beforeEach(() => {
  nav.replace.mockClear();
  nav.state.searchParam = null;
  nav.state.newParam = null;
  triggerInference.mockReset();
  rerunSearch.mockReset();
  deleteSearchJob.mockReset();
  createSearch.mockReset();
  updateProject.mockReset();
  cancelInference.mockReset();
});

// spec 2026-11-20-analyses-runs-2 F3 — page Analyse : état vide, carte corpus, runs, actions.
describe("inference page — spec 2026-11-20-2 F3 (corpora + runs)", () => {
  it("0 corpora: the S1 empty state with the CTA; no search=null API calls (R2)", async () => {
    mount({ user: {}, roleInProject: "LEAD", searches: [], articleCount: 33 });
    expect(await screen.findByText("corporaEmpty")).toBeInTheDocument();
    // the lead CTA opens the creation panel
    expect(screen.getByRole("button", { name: /pillNew/ })).toBeInTheDocument();
    // no API call resolves search=null (the backend 404s: never call it)
    await waitFor(() => expect(getInference).not.toHaveBeenCalled());
    expect(getProject).not.toHaveBeenCalled();
    expect(getOverview).toHaveBeenCalledWith("p1");
    expect(listProjects).toHaveBeenCalledWith();
  });

  it("0 corpora: the lead CTA opens the creation panel inline (R4)", async () => {
    mount({ user: {}, roleInProject: "LEAD", searches: [], articleCount: 33 });
    expect(await screen.findByText("corporaEmpty")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /pillNew/ }));
    // the panel is reachable in the S1 state: name field + create action
    expect(await screen.findByText("newCorpusTitle")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "createCorpus" })).toBeInTheDocument();
  });

  it("0 corpora: ?new=1 opens the creation panel in the S1 state (R4)", async () => {
    nav.state.newParam = "1";
    mount({ user: {}, roleInProject: "LEAD", searches: [], articleCount: 33 });
    expect(await screen.findByText("corporaEmpty")).toBeInTheDocument();
    expect(await screen.findByText("newCorpusTitle")).toBeInTheDocument();
  });

  it("stale tab-memory selection (deleted corpus): recovers to a live corpus, no permanent skeleton (R10)", async () => {
    sessionStorage.setItem(searchStoreKey("p1"), "dead-id");
    mount({ user: {}, roleInProject: "LEAD", searches: [search] });
    expect(
      await screen.findByText("corpusCard", {}, { timeout: 5000 })
    ).toBeInTheDocument();
    // the stale reference is dropped from tab memory
    expect(sessionStorage.getItem(searchStoreKey("p1"))).toBeNull();
  });

  it("no selection and no default (new projects, R9): the first corpus is the current one", async () => {
    // a project whose corpora were all created through the UI (none is_default)
    sessionStorage.clear();
    const only = { ...search, id: "s-only", is_default: false, name: "Corps" };
    mount({ user: {}, roleInProject: "LEAD", searches: [only] });
    expect(
      await screen.findByText("corpusCard", {}, { timeout: 5000 })
    ).toBeInTheDocument();
  });

  it("0 corpora, 0 articles: the Import CTA stays (spec clarte-2 R3)", async () => {
    mount({ user: {}, roleInProject: "LEAD", searches: [], articleCount: 0 });
    expect(await screen.findByText("importCta")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "importCta" })).toHaveAttribute("href", "/projects/p1/import");
  });

  it("corpus card: name, question, field labels, draft badge when never run", async () => {
    mount({ user: {}, roleInProject: "LEAD", searches: [draftSearch] });
    expect(await screen.findByText("corpusCard")).toBeInTheDocument();
    expect(screen.getByText("q?")).toBeInTheDocument();
    expect(screen.getByText("AI")).toBeInTheDocument();
    expect(screen.getByText("badgeDraft")).toBeInTheDocument();
    // the lead can edit the question
    expect(screen.getByRole("button", { name: "editQuestion" })).toBeInTheDocument();
  });

  it("runs: both runs render (name, status badge, model, x/y, date)", async () => {
    mount({ user: {}, roleInProject: "LEAD", jobs: [runningJob, completedJob] });
    // the two most recent runs, newest first
    expect(await screen.findByText("261120-2")).toBeInTheDocument();
    expect(screen.getByText("261101-1")).toBeInTheDocument();
    expect(screen.getByText("status_RUNNING")).toBeInTheDocument();
    expect(screen.getByText("status_COMPLETED")).toBeInTheDocument();
    // model (mono ref) + lot + analysed + date, one meta line per run
    expect(screen.getAllByText("runLot").length).toBe(2);
    expect(screen.getAllByText("runAnalysed").length).toBe(2);
    expect(screen.getAllByText("01/11/2026").length).toBeGreaterThan(0);
  });

  it("COMPLETED run: the breakdown sub-line (only the non-zero parts)", async () => {
    mount({ user: {}, roleInProject: "LEAD", jobs: [completedJob] });
    await screen.findByText("261101-1");
    expect(screen.getByText("runBreakAccepted")).toBeInTheDocument();
    expect(screen.getByText("runBreakRejected")).toBeInTheDocument();
    expect(screen.getByText("runBreakReview")).toBeInTheDocument();
    expect(screen.queryByText("runBreakNone")).not.toBeInTheDocument();
  });

  it("legacy run (no name): « sans nom » + the date", async () => {
    mount({ user: {}, roleInProject: "LEAD", jobs: [{ ...completedJob, name: null }] });
    expect(await screen.findByText("runUnnamed")).toBeInTheDocument();
  });

  it("cancelled run (FAILED + error=cancelled): the gray « Annulée » badge, not « Échec »", async () => {
    mount({ user: {}, roleInProject: "LEAD", jobs: [{ ...completedJob, status: "FAILED", error: "cancelled" }] });
    expect(await screen.findByText("status_CANCELLED")).toBeInTheDocument();
  });

  it("RUNNING run: the progress bar", async () => {
    mount({ user: {}, roleInProject: "LEAD", jobs: [runningJob] });
    await screen.findByText("261120-2");
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("Relancer menu: « Écraser ce résultat » + model → rerunSearch {model, overwrite_job_id}", async () => {
    rerunSearch.mockResolvedValue({ job_id: "j3", status: "PENDING" });
    mount({ user: {}, roleInProject: "LEAD", jobs: [completedJob] });
    await screen.findByText("261101-1");
    fireEvent.click(screen.getByRole("button", { name: /rerunMenu/ }));
    const menu = screen.getByRole("menu");
    // the model select defaults to the run's model, then switches to « other »
    const select = within(menu).getByRole("combobox");
    expect(select).toHaveValue("Kev");
    fireEvent.change(select, { target: { value: "other" } });
    fireEvent.click(within(menu).getByRole("menuitem", { name: /runOverwrite/ }));
    await waitFor(() =>
      expect(rerunSearch).toHaveBeenCalledWith("p1", "s1", "other", { overwrite_job_id: "j1" })
    );
  });

  it("Relancer menu: « Nouvelle analyse sur ce lot » → the panel opens with the run's model preselected", async () => {
    mount({ user: {}, roleInProject: "LEAD", jobs: [completedJob] });
    await screen.findByText("261101-1");
    fireEvent.click(screen.getByRole("button", { name: /rerunMenu/ }));
    fireEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: /runNamed/ }));
    expect(screen.getByText("panelTitle")).toBeInTheDocument();
    // the launch panel's main model is the run's model
    expect(screen.getByRole("combobox", { name: "stage2" })).toHaveValue("Kev");
  });

  it("Supprimer → Modal → deleteSearchJob → the list refreshes", async () => {
    deleteSearchJob.mockResolvedValue(undefined);
    mount({ user: {}, roleInProject: "LEAD", jobs: [completedJob] });
    await screen.findByText("261101-1");
    fireEvent.click(screen.getByRole("button", { name: "delete" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "delete" }));
    await waitFor(() => expect(deleteSearchJob).toHaveBeenCalledWith("p1", "s1", "j1"));
    await waitFor(() => expect(listSearchJobs.mock.calls.length).toBeGreaterThanOrEqual(2)); // refresh
  });

  it("launch panel, new mode: the name is sent, prefilled with the next YYMMDD-n", async () => {
    triggerInference.mockResolvedValue({ job_id: "j3", status: "PENDING" });
    mount({ user: {}, roleInProject: "LEAD", jobs: [completedJob] });
    await screen.findByText("261101-1");
    fireEvent.click(screen.getByRole("button", { name: "run" }));
    await waitFor(() => expect(triggerInference).toHaveBeenCalled());
    const payload = triggerInference.mock.calls[0][1];
    expect(payload.search_id).toBe("s1");
    expect(payload.model).toBe("Kev");
    // "261101-1" does not block today's rank 1
    expect(payload.name).toMatch(/^\d{6}-\d+$/);
    expect(payload).not.toHaveProperty("overwrite_job_id");
  });

  it("launch panel, overwrite mode: overwrite_job_id is sent and NO name", async () => {
    triggerInference.mockResolvedValue({ job_id: "j3", status: "PENDING" });
    mount({ user: {}, roleInProject: "LEAD", jobs: [completedJob] });
    await screen.findByText("261101-1");
    fireEvent.click(screen.getByRole("radio", { name: /outOverwrite/ }));
    const overwrite = screen.getByRole("combobox", { name: /outOverwrite/ });
    fireEvent.change(overwrite, { target: { value: "j1" } });
    fireEvent.click(screen.getByRole("button", { name: "run" }));
    await waitFor(() => expect(triggerInference).toHaveBeenCalled());
    const payload = triggerInference.mock.calls[0][1];
    expect(payload.overwrite_job_id).toBe("j1");
    expect(payload).not.toHaveProperty("name");
  });

  it("launch panel: the active run is disabled in the overwrite select", async () => {
    mount({ user: {}, roleInProject: "LEAD", jobs: [runningJob, completedJob] });
    await screen.findByText("261120-2");
    fireEvent.click(screen.getByRole("radio", { name: /outOverwrite/ }));
    const overwrite = screen.getByRole("combobox", { name: /outOverwrite/ });
    const options = Array.from(overwrite.querySelectorAll("option"));
    const active = options.find((o) => o.value === "j2");
    const done = options.find((o) => o.value === "j1");
    expect(active).toBeDefined();
    expect(active).toBeDisabled();
    expect(done).not.toBeDisabled();
  });

  it("stop the active run: confirmed through the Modal (never window.confirm)", async () => {
    const confirmSpy = jest.spyOn(window, "confirm").mockImplementation(() => true);
    cancelInference.mockResolvedValue(undefined);
    mount({ user: {}, roleInProject: "LEAD", jobs: [runningJob] });
    await screen.findByText("261120-2");
    fireEvent.click(screen.getByRole("button", { name: /stop/ }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "stop" }));
    await waitFor(() => expect(cancelInference).toHaveBeenCalledWith("p1"));
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});

// spec 2026-11-02-legacy-questions §4: the chain stays offered only while the project is busy.
describe("inference page — chaining", () => {
  it("the chain box is NOT offered while no analysis runs", async () => {
    mount({ user: {}, roleInProject: "LEAD", busy: null });
    await screen.findByText("corpusCard");
    expect(screen.queryByText("chainLabel")).not.toBeInTheDocument();
  });

  it("the chain box is offered while an analysis runs", async () => {
    mount({ user: {}, roleInProject: "LEAD", busy: { ...runningJob, search_id: "s1" } });
    await screen.findByText("chainLabel");
  });
});

// spec 2026-11-20-analyses-runs-2 F4 — création de corpus (« Nouveau corpus de questions »).
describe("inference page — spec 2026-11-20-2 F4 (create corpus)", () => {
  const saved = { ...search, id: "s2", is_default: false, name: "261103-1" };

  it("?new=1 opens the create panel; « Créer le corpus » → createSearch → diggy:searches", async () => {
    nav.state.newParam = "1";
    createSearch.mockResolvedValue(saved);
    mount({ user: {}, roleInProject: "LEAD" });
    expect(await screen.findByText("newCorpusTitle")).toBeInTheDocument();
    // the optional name is prefilled with the next free auto name
    const nameInput = screen.getByRole("textbox", { name: /nameLabel/ });
    // jest-dom 7 toHaveValue is strict equality (no RegExp): assert the shape on the raw value
    expect((nameInput as HTMLInputElement).value).toMatch(/^\d{6}-\d+$/);
    fireEvent.click(screen.getByRole("button", { name: "createCorpus" }));
    await waitFor(() => expect(createSearch).toHaveBeenCalled());
    const payload = createSearch.mock.calls[0][1];
    expect(payload.question).toBe("q?");
    expect(payload.name).toMatch(/^\d{6}-\d+$/);
    // the list refetches (diggy:searches dispatch) and the new corpus becomes the current one
    await waitFor(() => expect(listSearches.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() =>
      expect(nav.replace.mock.calls.map((c) => c[0]).some((u) => u?.includes("search=s2")))
    );
  });

  it("an empty name lets the backend auto-name (no name key)", async () => {
    nav.state.newParam = "1";
    createSearch.mockResolvedValue(saved);
    mount({ user: {}, roleInProject: "LEAD" });
    const nameInput = await screen.findByRole("textbox", { name: /nameLabel/ });
    fireEvent.change(nameInput, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "createCorpus" }));
    await waitFor(() => expect(createSearch).toHaveBeenCalled());
    const payload = createSearch.mock.calls[0][1];
    expect(payload).not.toHaveProperty("name");
  });
});

// spec 2026-11-20-analyses-runs-2 F6 — édition de corpus (question + champs + sections).
describe("inference page — spec 2026-11-20-2 F6 (edit corpus)", () => {
  it("« Éditer la question »: the editor is prefilled; save → PATCH ?search= with the question", async () => {
    updateProject.mockResolvedValue({});
    mount({ user: {}, roleInProject: "LEAD" });
    await screen.findByText("corpusCard");
    fireEvent.click(screen.getByRole("button", { name: "editQuestion" }));
    // the editor is prefilled with the corpus's question
    const editor = screen.getByRole("textbox", { name: /questionLabel/ });
    expect(editor).toHaveValue("q?");
    fireEvent.change(editor, { target: { value: "new question?" } });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(
        "p1",
        expect.objectContaining({ question: "new question?" }),
        "s1"
      )
    );
    // the project's searches refetch (the corpus changed)
    await waitFor(() => expect(listSearches.mock.calls.length).toBeGreaterThanOrEqual(2));
  });
});
