import { render, screen } from "@testing-library/react";
import { ArticleLinks } from "@/components/Certainty";

// i18n keys returned as-is (same convention as the other component tests).
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  t.rich = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});

describe("ArticleLinks — fiche identifier links (fix 2026-11-21)", () => {
  it("doi_url → the DOI link + its copy button", () => {
    render(<ArticleLinks article={{ doi: "10.1/x", doi_url: "https://doi.org/10.1/x" }} />);
    const doi = screen.getByRole("link", { name: "DOI ↗" });
    expect(doi).toHaveAttribute("href", "https://doi.org/10.1/x");
    expect(doi).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("button", { name: "copyDoi" })).toBeInTheDocument();
  });

  it("no doi_url but a PMID → a PubMed link to the record", () => {
    render(<ArticleLinks article={{ pmid: "596982" }} />);
    const pubmed = screen.getByRole("link", { name: "PubMed ↗" });
    expect(pubmed).toHaveAttribute("href", "https://pubmed.ncbi.nlm.nih.gov/596982/");
    expect(pubmed).toHaveAttribute("target", "_blank");
  });

  it("no identifier at all → nothing rendered", () => {
    const { container } = render(<ArticleLinks article={{}} />);
    expect(container.querySelector("a")).toBeNull();
  });
});
