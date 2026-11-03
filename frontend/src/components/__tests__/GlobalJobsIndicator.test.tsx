import { render, screen, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { GlobalJobsIndicator } from "@/components/GlobalJobsIndicator";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getGlobalActiveJobs: jest.fn(),
}));

const getGlobalActiveJobs = api.getGlobalActiveJobs as jest.Mock;

describe("GlobalJobsIndicator (§2)", () => {
  it("renders nothing when no job is active on the instance", async () => {
    getGlobalActiveJobs.mockResolvedValue({
      count: 0,
      by_kind: { import: 0, analysis: 0, benchmark: 0 },
    });
    const { container } = render(<GlobalJobsIndicator />);
    await waitFor(() => expect(getGlobalActiveJobs).toHaveBeenCalledTimes(1));
    expect(container.querySelector('[data-testid="global-jobs"]')).toBeNull();
  });

  it("shows the count, with the per-kind breakdown in the tooltip", async () => {
    getGlobalActiveJobs.mockResolvedValue({
      count: 2,
      by_kind: { import: 1, analysis: 1, benchmark: 0 },
    });
    render(<GlobalJobsIndicator />);

    const badge = await screen.findByTestId("global-jobs");
    expect(badge.textContent).toContain("2");
    const title = badge.getAttribute("title") ?? "";
    expect(title).toContain("import : 1");
    expect(title).toContain("analysis : 1");
    // the zero kind is not mentioned
    expect(title).not.toContain("benchmark : 0");
  });
});
