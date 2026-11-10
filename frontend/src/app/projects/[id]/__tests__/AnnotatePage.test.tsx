import { render, screen, waitFor } from "@testing-library/react";
import AnnotatePage from "@/app/projects/[id]/annotate/page";
import * as api from "@/lib/api";

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
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "p1" }),
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams("article=a1&queue=review&search=s1"),
  usePathname: () => "/projects/p1/annotate",
}));
// `mockKind` switches the current search kind (standard vs blind) between tests.
let mockKind = "standard";
jest.mock("@/lib/useSearch", () => ({
  useSearchId: () => "s1",
  useSearches: () => ({
    searches: [{ id: "s1", name: "S1", kind: mockKind }],
    current: { id: "s1", name: "S1", kind: mockKind },
  }),
}));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listArticles: jest.fn(),
  getProject: jest.fn(),
  getArticleWithPrediction: jest.fn(),
  submitAnnotation: jest.fn(),
}));

const listArticles = api.listArticles as jest.Mock;
const getProject = api.getProject as jest.Mock;
const getArticleWithPrediction = api.getArticleWithPrediction as jest.Mock;
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
  listArticles.mockReset().mockResolvedValue({ items: [{ id: "a1" }], total: 1 });
  getProject.mockReset().mockResolvedValue({ fields: [], text_sections: ["methods"] });
  getArticleWithPrediction.mockReset().mockResolvedValue({ article, prediction, review: null });
  submitAnnotation.mockReset().mockResolvedValue({ status: "VALIDATED" });
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
