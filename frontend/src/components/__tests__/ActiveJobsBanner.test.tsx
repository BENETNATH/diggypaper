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
});
