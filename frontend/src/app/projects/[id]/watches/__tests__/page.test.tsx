import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import WatchesPage from "../page";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
  usePathname: () => "/projects/p1/watches",
  useSearchParams: () => ({ get: () => null, toString: () => "" }),
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: "u1", email: "lead@lab.fr", role: "RESEARCHER" }, loading: false }),
}));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listWatches: jest.fn(),
  createWatch: jest.fn(),
  updateWatch: jest.fn(),
  deleteWatch: jest.fn(),
  runWatch: jest.fn(),
  listSearches: jest.fn(),
}));

const SEARCH: api.SearchInfo = {
  id: "s1",
  name: "Recherche principale",
  kind: "standard",
  is_default: true,
  question: "Q ?",
  fields: [{ key: "ai", label: "Uses AI", type: "boolean" }],
  text_sections: null,
  parent: null,
  parent_spec: null,
  created_at: null,
  created_by: "u1",
  lot_size: 10,
  predicted: 10,
  reviewed: 0,
  breakdown: {},
  to_review: 0,
  last_job: null,
  last_benchmark: null,
};

const WATCH: api.Watch = {
  id: "w1",
  source: "pubmed",
  source_name: "PubMed",
  query: "rat phenotyping",
  date_from: null,
  search: { id: "s1", name: "Recherche principale" },
  match_threshold: null,
  notify_all: false,
  criteria: null,
  combine: null,
  last_run_at: "2026-11-06T08:00:00Z",
  last_matched: {
    run_at: "2026-11-06T08:00:00Z",
    found: 3,
    imported: 2,
    matched: 1,
    matched_ids: ["a1"],
    matched_details: [{ id: "a1", title: "Nouvel article", pmid: "P1", confidence: 0.9 }],
    new: ["1"],
    job_id: "j1",
  },
  status: "ACTIVE",
  created_by: "Lead",
  created_at: "2026-11-01T00:00:00Z",
};

beforeEach(() => {
  (api.listSearches as jest.Mock).mockReset().mockResolvedValue([SEARCH]);
  (api.listWatches as jest.Mock).mockReset();
  (api.createWatch as jest.Mock).mockReset().mockResolvedValue(WATCH);
  (api.updateWatch as jest.Mock).mockReset().mockResolvedValue(WATCH);
  (api.deleteWatch as jest.Mock).mockReset().mockResolvedValue(undefined);
  (api.runWatch as jest.Mock).mockReset().mockResolvedValue(WATCH);
  window.confirm = jest.fn(() => true);
});

async function renderWith(watches: api.Watch[]) {
  (api.listWatches as jest.Mock).mockResolvedValue(watches);
  render(<WatchesPage />);
  await screen.findByRole("heading", { name: "title" });
}

describe("watches page", () => {
  it("empty state offers to create a watch", async () => {
    await renderWith([]);
    expect(screen.getByText("empty")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "new" })).toBeInTheDocument();
  });

  it("lists a watch with source, query, status and the last matching article", async () => {
    await renderWith([WATCH]);
    expect(screen.getByText("PubMed")).toBeInTheDocument();
    expect(screen.getByText("status_ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("rat phenotyping")).toBeInTheDocument();
    // last pass: the matching article (title + pmid) is visible
    expect(screen.getByText("Nouvel article")).toBeInTheDocument();
    expect(screen.getByText("P1")).toBeInTheDocument();
  });

  it("shows the criteria summary on a criteria-based watch", async () => {
    await renderWith([
      {
        ...WATCH,
        criteria: [
          { kind: "field", field_key: "ai", expected: true, conf_min: 0.7 },
          { kind: "free", question: "In vivo ?", expected: true },
        ],
        combine: "OR",
      },
    ]);
    expect(screen.getByText("2 criteria · OR")).toBeInTheDocument();
  });

  it("runs a pass on demand", async () => {
    await renderWith([WATCH]);
    fireEvent.click(screen.getByRole("button", { name: "runNow" }));
    await waitFor(() => expect(api.runWatch).toHaveBeenCalledWith("p1", "w1"));
  });

  it("pauses an active watch", async () => {
    await renderWith([WATCH]);
    fireEvent.click(screen.getByRole("button", { name: "pause" }));
    await waitFor(() =>
      expect(api.updateWatch).toHaveBeenCalledWith("p1", "w1", { status: "PAUSED" })
    );
  });

  it("resumes a paused watch", async () => {
    await renderWith([{ ...WATCH, status: "PAUSED" }]);
    expect(screen.getByText("status_PAUSED")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "resume" }));
    await waitFor(() =>
      expect(api.updateWatch).toHaveBeenCalledWith("p1", "w1", { status: "ACTIVE" })
    );
  });

  it("deletes a watch after confirmation", async () => {
    await renderWith([WATCH]);
    fireEvent.click(screen.getByRole("button", { name: "delete" }));
    await waitFor(() => expect(api.deleteWatch).toHaveBeenCalledWith("p1", "w1"));
    expect(window.confirm).toHaveBeenCalled();
  });
});

describe("watches create form", () => {
  it("creates a watch with the chosen search, source and query", async () => {
    await renderWith([]);
    fireEvent.click(screen.getByRole("button", { name: "new" }));
    const query = await screen.findByRole("textbox", { name: /query/ });
    fireEvent.change(query, { target: { value: "rat phenotype" } });
    fireEvent.click(screen.getByRole("button", { name: "create" }));
    await waitFor(() =>
      expect(api.createWatch).toHaveBeenCalledWith("p1", {
        search_id: "s1",
        source: "pubmed",
        query: "rat phenotype",
        date_from: null,
        notify_all: false,
        criteria: null,
        combine: null,
      })
    );
  });

  it("sends the alert criteria and the combine mode when built", async () => {
    await renderWith([]);
    fireEvent.click(screen.getByRole("button", { name: "new" }));
    const query = await screen.findByRole("textbox", { name: /query/ });
    fireEvent.change(query, { target: { value: "q" } });
    fireEvent.click(screen.getByRole("button", { name: "criteriaAdd" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: /critConfMin/ }), {
      target: { value: "0.7" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "notifyAll" }));
    fireEvent.click(screen.getByRole("button", { name: "create" }));
    await waitFor(() =>
      expect(api.createWatch).toHaveBeenCalledWith("p1", {
        search_id: "s1",
        source: "pubmed",
        query: "q",
        date_from: null,
        notify_all: true,
        criteria: [{ kind: "field", field_key: "ai", expected: true, conf_min: 0.7 }],
        combine: "AND",
      })
    );
  });

  it("refuses to create without a query", async () => {
    await renderWith([]);
    fireEvent.click(screen.getByRole("button", { name: "new" }));
    await screen.findByRole("textbox", { name: /query/ });
    fireEvent.submit(screen.getByRole("button", { name: "create" }).closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("requiredFields");
    expect(api.createWatch).not.toHaveBeenCalled();
  });
});
