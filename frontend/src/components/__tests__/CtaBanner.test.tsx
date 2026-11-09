import { render, screen, fireEvent } from "@testing-library/react";
import { SearchInfo } from "@/lib/api";
import { CtaBanner } from "@/components/CtaBanner";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});

const DERIVED = {
  id: "sB",
  name: "B",
  lot_size: 132,
} as unknown as SearchInfo;

function renderBanner(overrides: { nReview?: number; derived?: SearchInfo | null } = {}) {
  const onNewAnalysis = jest.fn();
  const onChat = jest.fn();
  render(
    <CtaBanner
      projectId="p1"
      searchId="sA"
      nResults={444}
      nReview={overrides.nReview ?? 87}
      derived={overrides.derived ?? null}
      onNewAnalysis={onNewAnalysis}
      onChat={onChat}
    />
  );
  return { onNewAnalysis, onChat };
}

test("state 1 — the four buttons, « Revoir » visible, new analysis primary", () => {
  renderBanner();
  const newAnalysis = screen.getByText("ctaNewAnalysis");
  expect(newAnalysis.className).toContain("bg-primary");
  expect(screen.getByText("ctaReview")).toBeInTheDocument();
  expect(screen.getByText("ctaChat")).toBeInTheDocument();
  expect(screen.getByText("ctaSeeAll")).toBeInTheDocument();
  // review → the review queue of the completed analysis
  expect(screen.getByText("ctaReview").closest("a")?.getAttribute("href")).toContain("annotate?queue=review");
  expect(screen.getByText("ctaReview").closest("a")?.getAttribute("href")).toContain("search=sA");
});

test("state 2 — 0 uncertain: the review button is masked", () => {
  renderBanner({ nReview: 0 });
  expect(screen.queryByText("ctaReview")).toBeNull();
  expect(screen.getByText("ctaChat")).toBeInTheDocument();
  expect(screen.getByText("ctaSeeAll")).toBeInTheDocument();
});

test("state 3 — B already derived from A: « Voir B » is primary, new analysis secondary", () => {
  renderBanner({ derived: DERIVED });
  const seeDerived = screen.getByText("ctaSeeDerived");
  expect(seeDerived.className).toContain("bg-primary");
  expect(seeDerived.closest("a")?.getAttribute("href")).toContain("search=sB");
  const newAnalysis = screen.getByText("ctaNewAnalysis");
  expect(newAnalysis.className).toContain("bg-surface");
  // exactly one primary button per banner
  const banner = screen.getByTestId("cta-banner");
  const primaries = [...banner.querySelectorAll("button, a")].filter((el) =>
    el.className.includes("bg-primary")
  );
  expect(primaries).toHaveLength(1);
});

test("clicking the actions fires the callbacks", () => {
  const { onNewAnalysis, onChat } = renderBanner();
  fireEvent.click(screen.getByText("ctaNewAnalysis"));
  expect(onNewAnalysis).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText("ctaChat"));
  expect(onChat).toHaveBeenCalledTimes(1);
});
