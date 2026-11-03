import { render, screen } from "@testing-library/react";
import * as api from "@/lib/api";
import { BenchmarkDisagreements } from "@/components/BenchmarkDisagreements";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listBenchmarkArticles: jest.fn(),
}));

const grid = (decision: Record<string, unknown>) => ({
  total: 1,
  page: 1,
  per_page: 25,
  items: [
    {
      article_id: "a1",
      title: "Article libre",
      gold: null,
      disagree: false,
      outputs: [{ model: "m1", variant: "direct", decision, error: null }],
    },
  ],
});

beforeEach(() => {
  (api.listBenchmarkArticles as jest.Mock).mockReset().mockResolvedValue(grid({}));
});

// Free-form prompts (benchmarks on a « mise au point » lot, no schema fields):
// the model answers in plain text (content / thought) — the card must show it,
// not only a bare "Pertinence —" row.
describe("BenchmarkDisagreements — free-form answers", () => {
  it("a decision without schema fields displays its content", async () => {
    (api.listBenchmarkArticles as jest.Mock).mockResolvedValue(
      grid({ content: "Le protocole est décrit page 3.", role: "assistant" })
    );
    render(<BenchmarkDisagreements projectId="p1" runId="b1" fields={[]} />);
    expect(await screen.findByText("Le protocole est décrit page 3.")).toBeInTheDocument();
  });

  it("falls back to 'thought' when 'content' is absent", async () => {
    (api.listBenchmarkArticles as jest.Mock).mockResolvedValue(
      grid({ thought: "Réflexion : le test dure 6 minutes." })
    );
    render(<BenchmarkDisagreements projectId="p1" runId="b1" fields={[]} />);
    expect(await screen.findByText("Réflexion : le test dure 6 minutes.")).toBeInTheDocument();
  });

  it("structured fields answered: the raw free text stays hidden", async () => {
    (api.listBenchmarkArticles as jest.Mock).mockResolvedValue(
      grid({ pertinence: "oui", content: "texte libre caché" })
    );
    render(
      <BenchmarkDisagreements
        projectId="p1"
        runId="b1"
        fields={[{ key: "pertinence", label: "Pertinence champ", type: "boolean" }]}
      />
    );
    expect(await screen.findByText("Pertinence champ")).toBeInTheDocument();
    expect(screen.queryByText("texte libre caché")).not.toBeInTheDocument();
  });

  it("answers stored under 'text' are shown", async () => {
    (api.listBenchmarkArticles as jest.Mock).mockResolvedValue(grid({ text: "Réponse via text." }));
    render(<BenchmarkDisagreements projectId="p1" runId="b1" fields={[]} />);
    expect(await screen.findByText("Réponse via text.")).toBeInTheDocument();
  });

  it("model-invented structured answers are rendered as pretty JSON", async () => {
    (api.listBenchmarkArticles as jest.Mock).mockResolvedValue(
      grid({ type_test: "unique", hauteur_eau_cm: 20, role: "assistant" })
    );
    render(<BenchmarkDisagreements projectId="p1" runId="b1" fields={[]} />);
    const block = await screen.findByText(/hauteur_eau_cm/);
    expect(block.textContent).toContain('"type_test": "unique"');
    // the role wrapper key is not shown
    expect(screen.queryByText(/"role"/)).not.toBeInTheDocument();
  });

  it("a provider error echoed in the content is shown as a model error", async () => {
    (api.listBenchmarkArticles as jest.Mock).mockResolvedValue(
      grid({ error: { code: 400, status: "FAILED_PRECONDITION", message: "temporairement bloqué" } })
    );
    render(<BenchmarkDisagreements projectId="p1" runId="b1" fields={[]} />);
    const el = await screen.findByText(/temporairement bloqué/);
    expect(el.className).toContain("text-danger");
    // not the raw JSON
    expect(screen.queryByText(/"status"/)).not.toBeInTheDocument();
  });

  it("an extracted 'error' field (string) is not a provider error", async () => {
    (api.listBenchmarkArticles as jest.Mock).mockResolvedValue(
      grid({ error: "texte extrait de l'article", confidence_score: 0.4 })
    );
    render(<BenchmarkDisagreements projectId="p1" runId="b1" fields={[]} />);
    expect(await screen.findByText(/texte extrait de l'article/)).toBeInTheDocument();
  });
});
