import { render, screen } from "@testing-library/react";
import { ResultsTable } from "@/components/ResultsTable";
import { Article, SchemaField } from "@/lib/api";

// i18n keys returned as-is (with their params): the test does not depend on the message files.
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(":")}` : key,
}));

const FIELDS: SchemaField[] = [
  { key: "design", label: "Design in vivo ?", type: "boolean" },
  { key: "species", label: "Espèce", type: "choice", options: ["souris", "rat"] },
];

const ARTICLE: Article = {
  id: "a1",
  title: "A mouse study",
  doi: "10.1/x",
  source: null,
  native_id: null,
  has_fulltext: true,
  fulltext_source: "upload",
  origin: "upload",
  origin_seeds: 0,
  prediction_status: "AUTO_ACCEPTED",
  confidence: 0.9,
  annotated: true,
  review: null,
  answers: [
    { key: "design", label: "Design in vivo ?", value: true, confidence: 0.88, source: "model" },
    { key: "species", label: "Espèce", value: "souris", confidence: 0.7, source: "model" },
  ],
} as Article;

// spec 2026-11-06-ux-refonte-v4 R3 — one column per question
describe("ResultsTable — one column per question (R3)", () => {
  it("renders one header column per question and one cell per answer", () => {
    render(<ResultsTable articles={[ARTICLE]} fields={FIELDS} hrefFor={() => "/x"} />);
    // headers = the question labels
    expect(screen.getByRole("columnheader", { name: "Design in vivo ?" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Espèce" })).toBeInTheDocument();
    // the article is still reachable (title link) with its status
    expect(screen.getByRole("link", { name: "A mouse study" })).toBeInTheDocument();
    expect(screen.getByText("status_AUTO_ACCEPTED")).toBeInTheDocument();
    // the answer cell is truncated with the full answer available on hover (existing tooltip)
    expect(screen.getAllByRole("tooltip")).toHaveLength(2);
    // "souris" is visible in the cell and repeated in the tooltip of the same cell
    expect(screen.getAllByText("souris").length).toBeGreaterThanOrEqual(1);
  });

  it("an answer missing for a question renders an empty cell (—)", () => {
    const noSpecies: Article = {
      ...ARTICLE,
      answers: [ARTICLE.answers![0]],
    } as Article;
    const { container } = render(<ResultsTable articles={[noSpecies]} fields={FIELDS} hrefFor={() => "/x"} />);
    expect(container.querySelectorAll("td")).toHaveLength(4); // article + 2 questions + status
    expect(container.textContent).toContain("—");
  });

  it("wide content scrolls inside its card, not outside the page", () => {
    const { container } = render(<ResultsTable articles={[ARTICLE]} fields={FIELDS} hrefFor={() => "/x"} />);
    const wrapper = container.querySelector("table")?.parentElement;
    expect(wrapper?.className).toContain("overflow-x-auto");
  });
});

// Fix 2026-11-21: the table meta line (biblio only) hid every identifier — a DOI link
// (with copy) when the article has one, a PubMed link otherwise.
describe("ResultsTable — identifier links (fix 2026-11-21)", () => {
  it("row with a doi_url → the DOI opens in a new tab + its copy button", () => {
    const a = {
      ...ARTICLE,
      source: "pubmed",
      native_id: "596982",
      pmid: "596982",
      doi_url: "https://doi.org/10.1/x",
    } as Article;
    render(<ResultsTable articles={[a]} fields={FIELDS} hrefFor={() => "/x"} />);
    const doi = screen.getByRole("link", { name: "DOI" });
    expect(doi).toHaveAttribute("href", "https://doi.org/10.1/x");
    expect(doi).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("button", { name: "copyDoi" })).toBeInTheDocument();
  });

  it("row without a doi_url but with a PMID → a PubMed link", () => {
    const a = { ...ARTICLE, doi: null, doi_url: null, pmid: "596982" } as Article;
    render(<ResultsTable articles={[a]} fields={FIELDS} hrefFor={() => "/x"} />);
    const pubmed = screen.getByRole("link", { name: "PubMed" });
    expect(pubmed).toHaveAttribute("href", "https://pubmed.ncbi.nlm.nih.gov/596982/");
    expect(pubmed).toHaveAttribute("target", "_blank");
  });

  it("row with no identifier at all → the meta line stays biblio-only", () => {
    const a = { ...ARTICLE, doi: null, doi_url: null, pmid: null, source: null } as Article;
    render(<ResultsTable articles={[a]} fields={FIELDS} hrefFor={() => "/x"} />);
    expect(screen.queryByRole("link", { name: "DOI" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "PubMed" })).not.toBeInTheDocument();
  });
});

// spec 2026-11-09-triage-seuils-fonds-champs R3/R4 — colored cell + corner human icon
describe("ResultsTable — colored cells and human icons (R3/R4)", () => {
  const BADGED: SchemaField[] = [{ key: "design", label: "Design in vivo ?", type: "boolean", badge: true }];

  const article = (patch: Partial<Article>): Article =>
    ({
      ...ARTICLE,
      field_confidences: { design: 0.9 },
      model_json: { design: true },
      human: null,
      ...patch,
    } as Article);

  it("conf ≥ accept threshold with the badge on → green cell + legend", () => {
    const { container } = render(
      <ResultsTable articles={[article({})]} fields={BADGED} accept={0.85} hrefFor={() => "/x"} />
    );
    expect(container.querySelector("td .bg-cell-good")).toBeInTheDocument();
    expect(container.querySelector("td .bg-cell-warn")).toBeNull();
    expect(screen.getByText("legendGood")).toBeInTheDocument();
    expect(screen.getByText("legendError")).toBeInTheDocument();
  });

  it("conf below the threshold (answer present) → amber cell", () => {
    const { container } = render(
      <ResultsTable
        articles={[article({ field_confidences: { design: 0.4 } })]}
        fields={BADGED}
        accept={0.85}
        hrefFor={() => "/x"}
      />
    );
    expect(container.querySelector("td .bg-cell-warn")).toBeInTheDocument();
    expect(container.querySelector("td .bg-cell-good")).toBeNull();
  });

  it("badge off → neutral cell, no legend", () => {
    const { container } = render(
      <ResultsTable
        articles={[article({})]}
        fields={[{ key: "design", label: "Design in vivo ?", type: "boolean" }]}
        accept={0.85}
        hrefFor={() => "/x"}
      />
    );
    expect(container.querySelector("td .bg-cell-good")).toBeNull();
    expect(container.querySelector("td .bg-cell-warn")).toBeNull();
    expect(screen.queryByText("legendGood")).toBeNull();
  });

  it("no field confidence (old predictions) → neutral (R2)", () => {
    const { container } = render(
      <ResultsTable
        articles={[article({ field_confidences: null })]}
        fields={BADGED}
        accept={0.85}
        hrefFor={() => "/x"}
      />
    );
    expect(container.querySelector("td .bg-cell-good")).toBeNull();
    expect(container.querySelector("td .bg-cell-warn")).toBeNull();
  });

  it("row in error → grey cell, no icon", () => {
    const a = article({
      prediction_status: "ERROR",
      human: { annotator: "Benoît Petit", decision_json: { design: true } },
    });
    const { container } = render(
      <ResultsTable articles={[a]} fields={BADGED} accept={0.85} hrefFor={() => "/x"} />
    );
    expect(container.querySelector("td .bg-surface-muted")).toBeInTheDocument();
    // no human-review icon on an error row (the certainty dot tooltip is not a cell icon)
    for (const sel of ["[title^='cellValidated']", "[title^='cellCorrected']", "[title='cellUnread']"]) {
      expect(container.querySelector(`td ${sel}`)).toBeNull();
    }
  });

  it("human validated the field → ✓ icon with initials + AI confidence tooltip", () => {
    const a = article({
      human: { annotator: "Benoît Petit", decision_json: { design: true } },
    });
    const { container } = render(
      <ResultsTable articles={[a]} fields={BADGED} accept={0.85} hrefFor={() => "/x"} />
    );
    const icon = container.querySelector("td [title^='cellValidated:BP']");
    expect(icon?.textContent).toBe("✓");
    expect(container.querySelector("td .text-success")).toBeInTheDocument();
  });

  it("human corrected the field → ! icon with « Corrigée » tooltip", () => {
    const a = article({
      human: { annotator: "C. Martin", decision_json: { design: false } },
    });
    const { container } = render(
      <ResultsTable articles={[a]} fields={BADGED} accept={0.85} hrefFor={() => "/x"} />
    );
    const icon = container.querySelector("td [title^='cellCorrected:CM']");
    expect(icon?.textContent).toBe("!");
    expect(container.querySelector("td .text-warning")).toBeInTheDocument();
  });

  it("model answered, not re-read → · (non relue)", () => {
    const { container } = render(
      <ResultsTable articles={[article({})]} fields={BADGED} accept={0.85} hrefFor={() => "/x"} />
    );
    const icon = container.querySelector("td [title='cellUnread']");
    expect(icon?.textContent).toBe("·");
  });
});
