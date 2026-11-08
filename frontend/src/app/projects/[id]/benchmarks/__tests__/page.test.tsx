import { Suspense } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { useAuth } from "@/lib/auth";
import BenchmarksPage from "@/app/projects/[id]/benchmarks/page";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
  useSearchParams: () => ({ get: () => null, toString: () => "" }),
  useRouter: () => ({ replace: jest.fn() }),
}));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/useSearch", () => ({
  useSearchId: () => "s1",
  useSearches: () => mockUseSearches(),
}));
const mockUseSearches = jest.fn();
// The panels stay in their loading state (never-resolving fetches) — the page
// test only checks the layout, not their content.
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listBenchmarks: jest.fn().mockResolvedValue([]),
  listModels: jest.fn().mockResolvedValue([]),
  cancelBenchmark: jest.fn(),
  deleteBenchmark: jest.fn(),
  rerunBenchmark: jest.fn(),
  retryBenchmarkErrors: jest.fn(),
  getComparison: jest.fn().mockImplementation(() => new Promise(() => {})),
  getSearchJobs: jest.fn().mockImplementation(() => new Promise(() => {})),
  getAgreement: jest.fn().mockImplementation(() => new Promise(() => {})),
}));
// The panels expose the searchId they were mounted for: the page test checks
// the scope, not their content.
jest.mock("@/components/ComparisonPanel", () => ({
  ComparisonPanel: ({ searchId }: { searchId: string }) => (
    <div data-testid="cmp-searchid">{searchId}</div>
  ),
}));
jest.mock("@/components/AgreementPanel", () => ({
  AgreementPanel: ({ searchId }: { searchId: string }) => (
    <div data-testid="agr-searchid">{searchId}</div>
  ),
}));

const mockUseAuth = useAuth as unknown as jest.Mock;

const DEFAULT_SEARCHES = {
  searches: [
    { id: "s1", name: "S1", kind: "standard", is_default: true },
    { id: "s2", name: "S2", kind: "standard", is_default: false },
  ],
  current: { id: "s1", name: "S1", kind: "standard", is_default: true },
};

function renderPage() {
  mockUseAuth.mockReturnValue({ user: { id: "u1" }, loading: false });
  mockUseSearches.mockReturnValue(DEFAULT_SEARCHES);
  return render(
    <Suspense fallback={null}>
      <BenchmarksPage />
    </Suspense>
  );
}

// spec 2026-11-08-ui-concordance-chat-recherche — Benchmarks = lieu unique des
// concordances (3 parties).
test("the concordance part carries a search selector and both panels", async () => {
  renderPage();
  const section = await screen.findByTestId("bm-concordance");
  expect(section).toBeInTheDocument();
  const select = screen.getByTestId("bm-concordance-search");
  // One option per standard search, the current one selected by default.
  const options = Array.from(select.querySelectorAll("option")).map((o) => o.value);
  expect(options).toEqual(["s1", "s2"]);
  expect(select).toHaveValue("s1");
  expect(screen.getByTestId("bm-comparison")).toBeInTheDocument();
  expect(screen.getByTestId("bm-agreement")).toBeInTheDocument();
});

test("switching the concordance search changes the scope", async () => {
  renderPage();
  const select = await screen.findByTestId("bm-concordance-search");
  fireEvent.change(select, { target: { value: "s2" } });
  expect(select).toHaveValue("s2");
  // Both panels stay rendered (remounted for the new search).
  expect(screen.getByTestId("bm-comparison")).toBeInTheDocument();
  expect(screen.getByTestId("bm-agreement")).toBeInTheDocument();
});

test("a deleted concordance search falls back to a live option", async () => {
  const { rerender } = renderPage();
  const select = await screen.findByTestId("bm-concordance-search");
  fireEvent.change(select, { target: { value: "s2" } });
  expect(screen.getByTestId("cmp-searchid")).toHaveTextContent("s2");
  // The list refreshes without s2 (deleted in the meantime): the stored
  // choice is stale and must not leave the panels pointed at a dead id.
  mockUseSearches.mockReturnValue({
    searches: [{ id: "s1", name: "S1", kind: "standard", is_default: true }],
    current: { id: "s1", name: "S1", kind: "standard", is_default: true },
  });
  rerender(
    <Suspense fallback={null}>
      <BenchmarksPage />
    </Suspense>
  );
  expect(screen.getByTestId("cmp-searchid")).toHaveTextContent("s1");
  expect(screen.getByTestId("agr-searchid")).toHaveTextContent("s1");
});
