import { Suspense } from "react";
import { render, screen } from "@testing-library/react";
import ArticlesPage from "@/app/projects/[id]/articles/page";
import * as api from "@/lib/api";
import { useAuth } from "@/lib/auth";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string) => key,
}));
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
  usePathname: () => "/projects/p1/articles",
  useRouter: () => ({ push: jest.fn() }),
}));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/useSearch", () => ({ useSearchId: () => "s1" }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getProject: jest.fn(),
  listArticles: jest.fn(),
  deleteArticle: jest.fn(),
  bulkDeleteArticles: jest.fn(),
  prismaUrl: jest.fn(() => "/prisma.svg"),
  prismaPdfUrl: jest.fn(() => "/prisma.pdf"),
}));
// the search/export side-panels are out of scope of this test
jest.mock("@/components/ExportMenu", () => ({ ExportMenu: () => null }));
jest.mock("@/components/SemanticSearchBox", () => ({ SemanticSearchBox: () => null }));

const mockUseAuth = useAuth as unknown as jest.Mock;
const getProject = api.getProject as jest.Mock;
const listArticles = api.listArticles as jest.Mock;

// spec 2026-11-09-clarte-2 — R3: empty corpus → the empty state offers the Import CTA
describe("articles page (spec clarte-2 R3)", () => {
  function mountNoCorpus(role_in_project: "LEAD" | "ANNOTATOR") {
    mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
    getProject.mockResolvedValue({ role_in_project, fields: [], article_count: 0 });
    listArticles.mockResolvedValue({ items: [], total: 0, stats: {} });
    return render(
      <Suspense fallback={null}>
        <ArticlesPage />
      </Suspense>
    );
  }

  it("no corpus → the empty state with the « Import » CTA for a lead", async () => {
    mountNoCorpus("LEAD");
    expect(await screen.findByText("empty")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "importCta" })).toHaveAttribute("href", "/projects/p1/import");
  });

  it("no corpus → the same empty state without any CTA for a member", async () => {
    mountNoCorpus("ANNOTATOR");
    expect(await screen.findByText("empty")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "importCta" })).not.toBeInTheDocument();
  });
});
