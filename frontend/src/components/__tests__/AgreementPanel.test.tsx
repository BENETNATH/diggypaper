import { render, screen, waitFor } from "@testing-library/react";
import * as api from "@/lib/api";
import { ModelInfo } from "@/lib/api";
import { AgreementPanel } from "@/components/AgreementPanel";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("@/lib/search", () => ({ withSearch: (p: string) => p }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getAgreement: jest.fn(),
}));

const getAgreement = api.getAgreement as jest.Mock;

const REVIEWERS = [
  { id: "rev-a", email: "rev-a@lab.fr", count: 4 },
  { id: "rev-b", email: "rev-b@lab.fr", count: 4 },
];

const PAYLOAD = {
  humans: {
    user_a: { id: "rev-a", email: "rev-a@lab.fr" },
    user_b: { id: "rev-b", email: "rev-b@lab.fr" },
    n: 4,
    n_agree: 3,
    acc: 0.75,
    kappa: 0.5,
    by_field: [
      { field: "include", n: 4, n_agree: 3, acc: 0.75, kappa: 0.5 },
      { field: "study_type", n: 4, n_agree: 2, acc: 0.5, kappa: 0.0 },
    ],
    disagreements: [
      {
        article_id: "a4",
        title: "Scube2 regrowth",
        value_a: false,
        value_b: true,
      },
    ],
  },
  ai: {
    model: "m1",
    user: { id: "rev-b", email: "rev-b@lab.fr" },
    n: 4,
    n_agree: 3,
    acc: 0.75,
    kappa: 0.5,
  },
  disagreements: [
    { article_id: "a4", title: "Scube2 regrowth", value_a: false, value_b: true },
  ],
  n_reviewers: 2,
  reviewers: REVIEWERS,
  summary: "Deux réviseurs ont indépendamment évalué 4 articles.",
};

const MODELS = [{ id: "m1-id", name: "m1" }] as unknown as ModelInfo[];

describe("AgreementPanel (§5: the payload is consumed as-is)", () => {
  it("shows the human aggregate, the per-field table and the PRISMA summary", async () => {
    getAgreement.mockResolvedValue(PAYLOAD);
    render(<AgreementPanel projectId="p1" searchId="s1" models={[]} />);
    await waitFor(() =>
      expect(getAgreement).toHaveBeenCalledWith("p1", "s1", {})
    );
    await screen.findByText("comparedArticles"); // the render flushed
    expect(screen.getAllByText("4").length).toBeGreaterThan(0); // n (and the counts)
    expect(screen.getAllByText("75.0%").length).toBeGreaterThan(0); // global + include field
    expect(screen.getByText("agreementRate")).toBeInTheDocument();
    expect(screen.getAllByText("0.50").length).toBeGreaterThan(0); // global + include field kappa
    expect(screen.getAllByText("kappa").length).toBeGreaterThan(0);
    // per-field table
    expect(screen.getByText("include")).toBeInTheDocument();
    expect(screen.getByText("study_type")).toBeInTheDocument();
    expect(screen.getAllByText("50.0%").length).toBeGreaterThan(0);
    // summary (in the <details> block)
    expect(screen.getByText("summary")).toBeInTheDocument();
  });

  it("lists the disagreements with a link to the article sheet", async () => {
    getAgreement.mockResolvedValue(PAYLOAD);
    render(<AgreementPanel projectId="p1" searchId="s1" models={[]} />);
    await waitFor(() => expect(getAgreement).toHaveBeenCalled());
    const link = await screen.findAllByText("Scube2 regrowth");
    expect(link.length).toBeGreaterThan(0);
    expect(link[0].closest("a")?.getAttribute("href")).toBe(
      "/projects/p1/annotate?article=a4"
    );
    expect(screen.getByText("disagreements")).toBeInTheDocument();
  });

  it("shows the AI block when a model is requested", async () => {
    getAgreement.mockResolvedValue(PAYLOAD);
    render(<AgreementPanel projectId="p1" searchId="s1" models={MODELS} />);
    await waitFor(() => expect(getAgreement).toHaveBeenCalled());
    expect(await screen.findByText("aiAgreement")).toBeInTheDocument();
    // "m1" appears in the selector (option) and in the AI block
    expect(screen.getAllByText("m1").length).toBeGreaterThan(1);
    // the model selector offers "None" + the available model
    expect(screen.getByText("noModel")).toBeInTheDocument();
  });

  it("says there is no common lot when the pair has n = 0", async () => {
    getAgreement.mockResolvedValue({
      ...PAYLOAD,
      humans: { ...PAYLOAD.humans, n: 0, n_agree: 0, acc: null, kappa: null, by_field: [], disagreements: [] },
      ai: null,
      disagreements: [],
    });
    render(<AgreementPanel projectId="p1" searchId="s1" models={[]} />);
    expect(await screen.findByText("noOverlap")).toBeInTheDocument();
  });

  it("renders nothing when nobody annotated the search", async () => {
    getAgreement.mockResolvedValue({
      ...PAYLOAD,
      humans: { ...PAYLOAD.humans, user_a: null, user_b: null, n: 0, n_agree: 0, acc: null, kappa: null, by_field: [], disagreements: [] },
      ai: null,
      disagreements: [],
      n_reviewers: 0,
      reviewers: [],
    });
    const { container } = render(<AgreementPanel projectId="p1" searchId="s1" models={[]} />);
    await waitFor(() => expect(getAgreement).toHaveBeenCalled());
    await waitFor(() => expect(container.firstChild).toBeNull());
  });
});
