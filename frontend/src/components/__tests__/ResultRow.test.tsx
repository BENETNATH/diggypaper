import { render, screen } from "@testing-library/react";
import { ResultRow } from "@/components/ResultRow";
import { Article } from "@/lib/api";

// i18n keys returned as-is (same convention as the other component tests).
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  t.rich = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});

// The FST case: a PubMed-imported row (source label shown) that also carries a DOI.
const BASE: Article = {
  id: "a1",
  title: "Behavioral despair in mice: a primary screening test for antidepressants.",
  first_author: "Porsolt, R. D.",
  journal: "Psychopharmacologia",
  pub_date: "1977-06-01",
  origin: "pubmed",
  source: "pubmed",
  native_id: "596982",
  pmid: "596982",
  has_fulltext: false,
} as unknown as Article;

const row = (a: Article) => render(<ResultRow a={a} href="/projects/p1/annotate?article=a1" />);

describe("ResultRow — identifier link (fix 2026-11-21)", () => {
  it("source=pubmed + DOI → the source label AND the DOI link + copy both appear", () => {
    row({ ...BASE, doi: "10.1/x", doi_url: "https://doi.org/10.1/x" });
    // The meta line is a separator-joined span row, so match the label with a regex.
    expect(screen.getByText(/PubMed PMID 596982/)).toBeInTheDocument();
    const doi = screen.getByRole("link", { name: "DOI" });
    expect(doi).toHaveAttribute("href", "https://doi.org/10.1/x");
    expect(doi).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("button", { name: "copyDoi" })).toBeInTheDocument();
  });

  it("source=pubmed, no DOI → the row offers a PubMed link to the record", () => {
    row(BASE);
    expect(screen.getByText(/PubMed PMID 596982/)).toBeInTheDocument();
    const pubmed = screen.getByRole("link", { name: "PubMed" });
    expect(pubmed).toHaveAttribute("href", "https://pubmed.ncbi.nlm.nih.gov/596982/");
    expect(pubmed).toHaveAttribute("target", "_blank");
  });

  it("no source, no DOI, no PMID → no identifier slot at all", () => {
    row({ ...BASE, source: null, native_id: null, pmid: null } as Article);
    expect(screen.queryByRole("link", { name: "DOI" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "PubMed" })).not.toBeInTheDocument();
  });
});
