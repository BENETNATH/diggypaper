import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { ActivityPanel } from "@/components/ActivityPanel";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getProjectActivity: jest.fn(),
  getAdminActivity: jest.fn(),
}));

const getProjectActivity = api.getProjectActivity as jest.Mock;
const getAdminActivity = api.getAdminActivity as jest.Mock;

beforeEach(() => {
  getProjectActivity.mockReset();
  getAdminActivity.mockReset();
});

const action = (over: Record<string, unknown> = {}) => ({
  type: "import",
  id: "a1",
  user: "Benoît",
  detail: "cancer AND mice",
  status: "COMPLETED",
  started_at: "2026-11-03T10:00:00Z",
  finished_at: "2026-11-03T10:05:00Z",
  ...over,
});

describe("ActivityPanel (§3)", () => {
  it("lists recent actions with the launcher and the detail (project mode)", async () => {
    getProjectActivity.mockResolvedValue({
      actions: [
        action(),
        action({
          type: "benchmark",
          id: "a2",
          user: "Lead Two",
          detail: "Question A",
          status: "RUNNING",
          finished_at: null,
        }),
      ],
    });
    render(<ActivityPanel projectId="p1" />);

    expect(await screen.findByText(/Benoît/)).toBeInTheDocument();
    expect(screen.getByText(/Lead Two/)).toBeInTheDocument();
    expect(screen.getByText("cancer AND mice")).toBeInTheDocument();
    expect(screen.getByText("benchmark")).toBeInTheDocument();
    expect(getProjectActivity).toHaveBeenCalledWith("p1");
  });

  it("shows the empty state", async () => {
    getProjectActivity.mockResolvedValue({ actions: [] });
    render(<ActivityPanel projectId="p1" />);
    expect(await screen.findByText("empty")).toBeInTheDocument();
  });

  it("shows the error with a retry that re-fetches", async () => {
    getProjectActivity.mockRejectedValueOnce(new Error("boom"));
    render(<ActivityPanel projectId="p1" />);

    const retry = await screen.findByText("retry");
    getProjectActivity.mockClear();
    getProjectActivity.mockResolvedValueOnce({ actions: [] });
    fireEvent.click(retry);
    await waitFor(() => expect(getProjectActivity).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("empty")).toBeInTheDocument();
  });

  it("uses the global endpoint and shows the project title (admin mode)", async () => {
    getAdminActivity.mockResolvedValue({
      actions: [action({ project_title: "FST mouse" })],
    });
    render(<ActivityPanel global />);

    expect(await screen.findByText(/Benoît/)).toBeInTheDocument();
    expect(screen.getByText(/FST mouse/)).toBeInTheDocument();
    expect(getAdminActivity).toHaveBeenCalled();
    expect(getProjectActivity).not.toHaveBeenCalled();
  });
});
