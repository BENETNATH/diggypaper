import { act, render, screen, waitFor } from "@testing-library/react";
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

  it("shows the count, with the per-kind breakdown visible (audit 2026-11-10 P3-12: was title-only)", async () => {
    getGlobalActiveJobs.mockResolvedValue({
      count: 2,
      by_kind: { import: 1, analysis: 1, benchmark: 0 },
    });
    render(<GlobalJobsIndicator />);

    const badge = await screen.findByTestId("global-jobs");
    expect(badge.textContent).toContain("2");
    // the breakdown is in the accessible text, not only in a native tooltip
    expect(badge.textContent).toContain("import : 1");
    expect(badge.textContent).toContain("analysis : 1");
    // the zero kind is not mentioned
    expect(badge.textContent).not.toContain("benchmark : 0");
  });

  it("keeps polling at count 0 and re-shows the banner when a job returns (audit 2026-11-10 P3-4)", async () => {
    jest.useFakeTimers();
    getGlobalActiveJobs.mockClear(); // the previous tests already consumed ticks
    getGlobalActiveJobs
      .mockResolvedValueOnce({ count: 1, by_kind: { import: 1, analysis: 0, benchmark: 0 } })
      .mockResolvedValue({ count: 0, by_kind: { import: 0, analysis: 0, benchmark: 0 } });
    const { container } = render(<GlobalJobsIndicator />);
    // flush the immediate first tick (async mock)
    await act(async () => {});
    expect(screen.getByTestId("global-jobs").textContent).toContain("1");
    // one tick later count → 0: hidden, but the poll must go on (not stop).
    await act(async () => {
      jest.advanceTimersByTime(30000);
    });
    expect(container.querySelector('[data-testid="global-jobs"]')).toBeNull();
    // a later tick re-fetches; when a job comes back the banner reappears without a remount.
    getGlobalActiveJobs.mockResolvedValue({ count: 1, by_kind: { import: 1, analysis: 0, benchmark: 0 } });
    await act(async () => {
      jest.advanceTimersByTime(30000);
    });
    expect(screen.getByTestId("global-jobs").textContent).toContain("1");
    getGlobalActiveJobs.mockClear();
    jest.useRealTimers();
  });
});
