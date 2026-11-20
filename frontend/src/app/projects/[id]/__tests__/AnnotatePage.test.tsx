import { render, screen, waitFor } from "@testing-library/react";
import AnnotatePage from "@/app/projects/[id]/annotate/page";
import * as api from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useSearches } from "@/lib/useSearch";

type Catalog = Record<string, Record<string, string>>;

// Real fr messages (audit 2026-11-10 P1-1: the accept button must exist, in French).
/* eslint-disable @typescript-eslint/no-require-imports */
jest.mock("next-intl", () => {
  const fr = require("../../../../messages/fr.json") as Catalog;
  return {
    useLocale: () => "fr",
    useTranslations: (ns: string) => (key: string, params?: Record<string, unknown>) => {
      const t = (fr[ns] ?? {})[key];
      if (t == null) return key;
      return String(t).replace(/\{(\w+)\}/g, (m: string, k: string) =>
        String((params ?? {})[k] ?? m)
      );
    },
  };
});
/* eslint-enable @typescript-eslint/no-require-imports */
// `mockKind` switches the current search kind (standard vs blind) between tests.
let mockKind = "standard";
// spec 2026-11-20-fiche-article-corpus: the page is tested both in search mode
// (mockHasCorpora = true, the scoped default of the older tests) and corpus mode
// (no search at all, URL without ?search=).
let mockHasCorpora = true;
let mockUrlParams = "article=a1&queue=review&search=s1";
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams(mockUrlParams),
  usePathname: () => "/projects/p1/annotate",
}));
jest.mock("@/lib/useSearch", () => ({
  useSearchId: () => "s1",
  useSearches: jest.fn(() => ({
    searches: mockHasCorpora ? [{ id: "s1", name: "S1", kind: mockKind }] : [],
    current: mockHasCorpora ? { id: "s1", name: "S1", kind: mockKind } : undefined,
  })),
}));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listArticles: jest.fn(),
  getProject: jest.fn(),
  getArticleWithPrediction: jest.fn(),
  getArticle: jest.fn(),
  submitAnnotation: jest.fn(),
  getOverview: jest.fn(),
  listProjects: jest.fn(),
}));

const mockUseAuth = useAuth as unknown as jest.Mock;
const listArticles = api.listArticles as jest.Mock;
const getProject = api.getProject as jest.Mock;
const getArticleWithPrediction = api.getArticleWithPrediction as jest.Mock;
const getArticle = api.getArticle as jest.Mock;
const submitAnnotation = api.submitAnnotation as jest.Mock;

const prediction = {
  id: "pr1",
  article_id: "a1",
  prompt_template_id: null,
  model_used: "qwen",
  model_identifier: null,
  model_version: null,
  decision_json: { pertinence: "oui" },
  confidence_score: 0.9,
  evidence_quotes: ["abc"],
  status: "NEEDS_HUMAN_REVIEW",
};

const article = {
  id: "a1",
  pmid: null,
  pmcid: null,
  doi: null,
  title: "Titre",
  abstract: null,
  methods_text: null,
  text: "abc def",
  has_fulltext: true,
  fulltext_source: "pubmed",
};

beforeEach(() => {
  mockKind = "standard";
  mockHasCorpora = true;
  mockUrlParams = "article=a1&queue=review&search=s1";
  mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
  listArticles.mockReset().mockResolvedValue({ items: [{ id: "a1" }], total: 1 });
  getProject.mockReset().mockResolvedValue({ fields: [], text_sections: ["methods"] });
  getArticleWithPrediction.mockReset().mockResolvedValue({ article, prediction, review: null });
  submitAnnotation.mockReset().mockResolvedValue({ status: "VALIDATED" });
  getArticle.mockReset().mockResolvedValue(article);
  (api.getOverview as jest.Mock)
    .mockReset()
    .mockResolvedValue({ articles: 0 });
  (api.listProjects as jest.Mock)
    .mockReset()
    .mockResolvedValue([{ id: "p1", role_in_project: "LEAD" }]);
});

// audit 2026-11-10 P1-1: accept/reject used to be swipe-only (WCAG 2.1.1).
// The accept action must be a real button; swipe stays as a shortcut.
describe("AnnotatePage — keyboard accept", () => {
  it("renders an accept button for the model answer and submits it on click", async () => {
    render(<AnnotatePage />);
    const btn = await screen.findByRole("button", { name: "Confirmer la réponse du modèle" });
    expect(btn).toBeEnabled();
    btn.click();
    await waitFor(() => expect(submitAnnotation).toHaveBeenCalledTimes(1));
    const [pid, aid, annotation] = submitAnnotation.mock.calls[0];
    expect(pid).toBe("p1");
    expect(aid).toBe("a1");
    expect(annotation).toMatchObject({ pertinence: "oui" });
  });

  it("no accept button in blind mode before re-review (the model answer is hidden)", async () => {
    mockKind = "blind";
    render(<AnnotatePage />);
    await screen.findByText("Titre", { selector: "h1" });
    expect(
      screen.queryByRole("button", { name: "Confirmer la réponse du modèle" })
    ).not.toBeInTheDocument();
  });

  // caveman-review 2026-11-10: a double-click on confirm must not double-submit.
  it("double-click on confirm submits only once (in-flight guard)", async () => {
    submitAnnotation.mockImplementation(() => new Promise(() => {})); // never settles
    render(<AnnotatePage />);
    const findConf = () =>
      screen.queryAllByRole("button", { name: "Confirmer la réponse du modèle" })[0];
    (await screen.findByRole("button", { name: "Confirmer la réponse du modèle" })).click();
    // the in-flight state disables the button (React flushes after the handler yields)
    await waitFor(() => expect(findConf()).toBeDisabled());
    findConf()?.click(); // second click while the first is in flight
    await waitFor(() => expect(submitAnnotation).toHaveBeenCalledTimes(1));
    expect(submitAnnotation).toHaveBeenCalledTimes(1); // still exactly one
  });
});

// spec 2026-11-20-fiche-article-corpus R4: corpus mode (no search) — the article sheet
// unlocks the corpus-view row click: the document renders, annotation does not.
describe("AnnotatePage — corpus mode (no search)", () => {
  it("articleId → the article renders (full text, lead buttons), getArticle SANS searchId, no form, no stepper", async () => {
    mockHasCorpora = false;
    mockUrlParams = "article=a1";
    (api.getOverview as jest.Mock).mockResolvedValue({ articles: 82 });
    render(<AnnotatePage />);
    expect(await screen.findByText("Titre", { selector: "h1" })).toBeInTheDocument();
    // fetched without any search id
    expect(getArticle).toHaveBeenCalledWith("p1", "a1");
    // the full text and the lead buttons of the document column
    expect(screen.getByText("abc def")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Relancer la recherche du texte intégral" })
    ).toBeInTheDocument();
    expect(screen.getByText("Remplacer par un PDF")).toBeInTheDocument();
    // no annotation form, no queue/stepper, no model banner
    expect(
      screen.queryByRole("button", { name: "Enregistrer la relecture" })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "Navigation entre articles" })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("Aucune réponse du modèle pour cette recherche.")
    ).not.toBeInTheDocument();
    expect(listArticles).not.toHaveBeenCalled();
    // back to the corpus view, no search param
    expect(screen.getByRole("link", { name: /Retour au corpus/ })).toHaveAttribute(
      "href",
      "/projects/p1/articles"
    );
  });

  it("no articleId → the link to the corpus view (count), not the dead-end empty state", async () => {
    mockHasCorpora = false;
    mockUrlParams = "queue=review";
    (api.getOverview as jest.Mock).mockResolvedValue({ articles: 82 });
    render(<AnnotatePage />);
    const link = await screen.findByRole("link", { name: /Voir le corpus/ });
    expect(link).toHaveAttribute("href", "/projects/p1/articles");
    expect(screen.getByText(/82 articles/)).toBeInTheDocument();
    expect(screen.queryByText("Aucun corpus de questions")).not.toBeInTheDocument();
    expect(getArticle).not.toHaveBeenCalled();
  });

  it("does not fire the review-queue fetch while the search list is still loading (no stale error in corpus mode)", async () => {
    mockHasCorpora = false;
    mockUrlParams = "article=a1";
    (api.getOverview as jest.Mock).mockResolvedValue({ articles: 82 });
    const mockUseSearches = useSearches as unknown as jest.Mock;
    const defaultImpl = mockUseSearches.getMockImplementation();
    // first render: the search list is still loading (searches = null)
    mockUseSearches.mockReturnValue({ searches: null, current: undefined });
    try {
      const { rerender } = render(<AnnotatePage />);
      // the search-scoped queue fetch must not fire before the search state is known
      await waitFor(() => expect(mockUseSearches).toHaveBeenCalled());
      expect(listArticles).not.toHaveBeenCalled();
      // the search list resolves: no search → the corpus branch, no stale error
      mockUseSearches.mockReturnValue({ searches: [], current: undefined });
      rerender(<AnnotatePage />);
      expect(await screen.findByText("Titre", { selector: "h1" })).toBeInTheDocument();
      expect(listArticles).not.toHaveBeenCalled();
      expect(
        screen.queryByText(/no analysis in this project|aucune analyse/i)
      ).not.toBeInTheDocument();
    } finally {
      if (defaultImpl) mockUseSearches.mockImplementation(defaultImpl);
    }
  });
});
