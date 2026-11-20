import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { ComparisonPanel } from "@/components/ComparisonPanel";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("@/lib/search", () => ({ withSearch: (p: string) => p }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listSearchJobs: jest.fn(),
  getComparison: jest.fn(),
}));

const listSearchJobs = api.listSearchJobs as jest.Mock;
const getComparison = api.getComparison as jest.Mock;

const JOBS = [
  {
    id: "job-new",
    project_id: "p1",
    prompt_template_id: null,
    model_used: "Nova-7B",
    search_id: "s1",
    prefilter_model: null,
    model_info: { main: { name: "Nova-7B", identifier: "x", version: "v2" }, prefilter: null },
    status: "COMPLETED",
    result: null,
    error: null,
    created_at: "2026-11-07T11:00:00Z",
    completed_at: "2026-11-07T11:05:00Z",
  },
  {
    id: "job-old",
    project_id: "p1",
    prompt_template_id: null,
    model_used: "Kev-4B",
    search_id: "s1",
    prefilter_model: null,
    model_info: { main: { name: "Kev-4B", identifier: "x", version: "v1" }, prefilter: null },
    status: "COMPLETED",
    result: null,
    error: null,
    created_at: "2026-11-07T09:00:00Z",
    completed_at: "2026-11-07T09:10:00Z",
  },
];

const PAYLOAD = {
  n: 3,
  n_agree: 2,
  agreement: 0.6667,
  model_a: { name: "Kev-4B", version: "v1" },
  model_b: { name: "Nova-7B", version: "v2" },
  n_excluded: 1,
  rows: [
    {
      article_id: "a1",
      title: "Zebrafish cryo",
      decision_a: true,
      decision_b: true,
      confidence_a: 0.9,
      confidence_b: 0.8,
      agree: true,
    },
    {
      article_id: "a2",
      title: "Scube2 regrowth",
      decision_a: "oui",
      decision_b: "exclude",
      confidence_a: null,
      confidence_b: 0.4,
      agree: false,
    },
    {
      article_id: "a3",
      title: null,
      decision_a: "exclude",
      decision_b: "exclude",
      confidence_a: 0.7,
      confidence_b: 0.7,
      agree: true,
    },
  ],
  disagreements: [
    { article_id: "a2", title: "Scube2 regrowth", decision_a: "oui", decision_b: "exclude" },
  ],
};

describe("ComparisonPanel (§5: the payload is consumed as-is)", () => {
  it("shows the aggregate and the article-by-article table from the payload", async () => {
    listSearchJobs.mockResolvedValue({ jobs: JOBS });
    getComparison.mockResolvedValue(PAYLOAD);
    render(<ComparisonPanel projectId="p1" searchId="s1" />);
    // default pair: the two most recent jobs
    await waitFor(() =>
      expect(getComparison).toHaveBeenCalledWith("p1", "s1", "job-new", "job-old")
    );
    // aggregate: n, agreement %, both models
    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.getByText("comparedArticles")).toBeInTheDocument();
    expect(screen.getByText("66.7%")).toBeInTheDocument();
    expect(screen.getByText("agreement")).toBeInTheDocument();
    expect(screen.getByText("Kev-4B")).toBeInTheDocument();
    expect(screen.getByText("Nova-7B")).toBeInTheDocument();
    // table rows: title, both decisions, confidences, agreement mark
    expect(screen.getByText("Zebrafish cryo")).toBeInTheDocument();
    expect(screen.getAllByText("true").length).toBe(2);
    expect(screen.getByText("oui")).toBeInTheDocument();
    expect(screen.getByText("0.90 / 0.80")).toBeInTheDocument();
    expect(screen.getByText("— / 0.40")).toBeInTheDocument();
    expect(screen.getAllByText("✅").length).toBe(2);
    expect(screen.getByText("⚠️")).toBeInTheDocument();
    // excluded lot is reported
    expect(screen.getByText("excluded", { exact: false })).toBeInTheDocument();
  });

  it("lists the disagreements with a link to the article sheet", async () => {
    listSearchJobs.mockResolvedValue({ jobs: JOBS });
    getComparison.mockResolvedValue(PAYLOAD);
    render(<ComparisonPanel projectId="p1" searchId="s1" />);
    await waitFor(() => expect(getComparison).toHaveBeenCalled());
    const link = await screen.findAllByText("Scube2 regrowth");
    expect(link.length).toBeGreaterThan(0);
    expect(link[0].closest("a")?.getAttribute("href")).toBe(
      "/projects/p1/annotate?article=a2"
    );
    expect(screen.getByText("disagreements")).toBeInTheDocument();
  });

  it("says there is nothing in common when the lots are disjoint (n = 0)", async () => {
    listSearchJobs.mockResolvedValue({ jobs: JOBS });
    getComparison.mockResolvedValue({ ...PAYLOAD, n: 0, n_agree: 0, agreement: null, n_excluded: 4, rows: [], disagreements: [] });
    render(<ComparisonPanel projectId="p1" searchId="s1" />);
    expect(await screen.findByText("noCommon")).toBeInTheDocument();
  });

  it("renders nothing when the search has a single analysis", async () => {
    listSearchJobs.mockResolvedValue({ jobs: JOBS.slice(1) });
    const { container } = render(<ComparisonPanel projectId="p1" searchId="s1" />);
    await waitFor(() => expect(listSearchJobs).toHaveBeenCalled());
    await waitFor(() => expect(container.firstChild).toBeNull());
  });
});

// audit 2026-11-10 P2-12 — the article-by-article table is paginated (50/page, client-side)
describe("ComparisonPanel — table pagination (audit 2026-11-10 P2-12)", () => {
  const manyRows = Array.from({ length: 52 }, (_, i) => ({
    article_id: `a${i + 1}`,
    title: `Article ${i + 1}`,
    decision_a: true,
    decision_b: true,
    confidence_a: 0.9,
    confidence_b: 0.8,
    agree: true,
  }));

  it("shows the first page only; « next » reaches the rest", async () => {
    listSearchJobs.mockResolvedValue({ jobs: JOBS });
    getComparison.mockResolvedValue({ ...PAYLOAD, n: 52, rows: manyRows, disagreements: [] });
    render(<ComparisonPanel projectId="p1" searchId="s1" />);
    expect(await screen.findByText("Article 1")).toBeInTheDocument();
    expect(screen.queryByText("Article 51")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "next" }));
    expect(await screen.findByText("Article 51")).toBeInTheDocument();
    expect(screen.queryByText("Article 1")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "next" })).toBeDisabled();
  });
});
