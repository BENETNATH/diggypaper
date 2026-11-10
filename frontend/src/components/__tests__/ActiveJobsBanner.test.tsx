import { render, screen, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { ActiveJobsBanner } from "@/components/ActiveJobsBanner";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getActiveProjectJobs: jest.fn(),
}));

const getActiveProjectJobs = api.getActiveProjectJobs as jest.Mock;

describe("ActiveJobsBanner (§1)", () => {
  it("renders nothing when no job is active", async () => {
    getActiveProjectJobs.mockResolvedValue({ jobs: [] });
    const { container } = render(<ActiveJobsBanner projectId="p1" />);
    await waitFor(() => expect(getActiveProjectJobs).toHaveBeenCalledTimes(1));
    expect(container.querySelector('[data-testid="active-jobs"]')).toBeNull();
  });

  it("lists each active job with its kind, detail and progress", async () => {
    getActiveProjectJobs.mockResolvedValue({
      jobs: [
        {
          kind: "import",
          id: "j1",
          status: "RUNNING",
          detail: "cancer AND mice",
          progress: null,
          models: null,
          search_id: null,
          started_at: null,
        },
        {
          kind: "analysis",
          id: "j2",
          status: "PENDING",
          detail: "Question A",
          progress: null,
          models: ["Kev-4B"],
          search_id: "s1",
          started_at: null,
        },
        {
          kind: "benchmark",
          id: "j3",
          status: "RUNNING",
          detail: "Question A",
          progress: { done: 3, total: 10 },
          models: ["m1", "m2"],
          search_id: "s1",
          started_at: null,
        },
      ],
    });
    render(<ActiveJobsBanner projectId="p1" />);

    expect(await screen.findByTestId("active-jobs")).toBeInTheDocument();
    expect(getActiveProjectJobs).toHaveBeenCalledWith("p1");
    expect(screen.getByText("import")).toBeInTheDocument();
    expect(screen.getByText("analysis")).toBeInTheDocument();
    expect(screen.getByText("benchmark")).toBeInTheDocument();
    expect(screen.getByText("cancer AND mice")).toBeInTheDocument();
    // « Question A » is shared by the analysis and the benchmark
    expect(screen.getAllByText("Question A")).toHaveLength(2);
    expect(screen.getByText("3/10")).toBeInTheDocument();
    expect(screen.getByText("m1, m2")).toBeInTheDocument();
  });

  it("spec clarte-1 R4: fulltext job labelled kind_fulltext, mini progress bar per row, « Ouvrir » to the import page", async () => {
    getActiveProjectJobs.mockResolvedValue({
      jobs: [
        {
          kind: "fulltext",
          id: "j4",
          status: "RUNNING",
          detail: null,
          progress: { done: 212, total: 334 },
          models: null,
          search_id: null,
          started_at: null,
        },
        {
          kind: "analysis",
          id: "j5",
          status: "RUNNING",
          detail: null,
          progress: { done: 6, total: 33 },
          models: null,
          search_id: "s1",
          started_at: null,
        },
      ],
    });
    render(<ActiveJobsBanner projectId="p1" />);

    expect(await screen.findByTestId("active-jobs")).toBeInTheDocument();
    // « Texte intégral » (jobs.kind_fulltext), not the wrong « import » label
    expect(screen.getByText("kind_fulltext")).toBeInTheDocument();
    const bars = screen.getAllByRole("progressbar");
    expect(bars).toHaveLength(2);
    expect(bars[0]).toHaveAttribute("aria-valuenow", "212");
    expect(bars[0]).toHaveAttribute("aria-valuemax", "334");
    expect(bars[1]).toHaveAttribute("aria-valuenow", "6");
    expect(screen.getByText("212/334")).toBeInTheDocument();
    expect(screen.getByText("6/33")).toBeInTheDocument();
    const opens = screen.getAllByRole("link", { name: "open" });
    expect(opens.map((a) => a.getAttribute("href"))).toEqual([
      "/projects/p1/import",
      "/projects/p1/inference?search=s1",
    ]);
  });
});
