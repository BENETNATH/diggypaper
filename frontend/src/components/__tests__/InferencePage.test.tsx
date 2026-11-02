import { Suspense } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
  useSearchParams: () => ({ get: () => null, toString: () => "" }),
  usePathname: () => "/projects/p1/inference",
  useRouter: () => ({ replace: jest.fn() }),
}));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
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
}));

const mockUseAuth = useAuth as unknown as jest.Mock;
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
};

const model = { id: "m1", name: "Kev" } as unknown as api.ModelInfo;
const runningJob = {
  id: "j1",
  project_id: "p1",
  search_id: "s1",
  status: "RUNNING",
  result: { done: 1, total: 10 },
} as unknown as api.InferenceJob;

function mount(overrides: {
  user: { role?: string };
  roleInProject: string | null;
  busy?: api.InferenceJob | null;
  searches?: SearchInfo[];
}) {
  mockUseAuth.mockReturnValue({ user: overrides.user, loading: false });
  listSearches.mockResolvedValue(overrides.searches ?? [search]);
  getProject.mockResolvedValue({
    role_in_project: overrides.roleInProject,
    question_set: true,
    question: "q?",
    fields: search.fields,
    text_sections: ["methods"],
  });
  listModels.mockResolvedValue([model]);
  listGold.mockResolvedValue([]);
  (api.previewSelection as jest.Mock).mockResolvedValue({ count: 4 });
  getInference.mockImplementation((_p: string, _s?: string, all?: boolean) =>
    Promise.resolve(all ? (overrides.busy ?? null) : null)
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
    getInference.mockImplementation((_p: string, s?: string, all?: boolean) =>
      all
        ? Promise.resolve(null)
        : s
          ? Promise.reject(new api.ApiError(404, "search not found"))
          : Promise.resolve(null)
    );
    sessionStorage.setItem("diggy:search:p1", "deleted-id");
    // a visible (touched) row stays in the list, pointing at a stored search that no longer exists
    mount({
      user: { role: "RESEARCHER" },
      roleInProject: "LEAD",
      searches: [{ ...search, predicted: 1 }],
    });
    await panelOpen();
    // no crash, no job shown for the dead search
    expect(screen.queryByText("progressTitle")).not.toBeInTheDocument();
    expect(screen.getByText("S1")).toBeInTheDocument();
    sessionStorage.removeItem("diggy:search:p1");
  });
});
