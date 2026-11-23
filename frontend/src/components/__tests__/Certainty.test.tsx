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

  it("pdfHref → the stored-PDF link, before the external links (P-3)", () => {
    render(
      <ArticleLinks
        article={{ doi: "10.1/x", doi_url: "https://doi.org/10.1/x", fulltext_url: "https://ft.example" }}
        pdfHref="/api/v1/projects/p1/articles/a1/pdf"
      />
    );
    const pdf = screen.getByRole("link", { name: "PDF ↗" });
    expect(pdf).toHaveAttribute("href", "/api/v1/projects/p1/articles/a1/pdf");
    expect(pdf).toHaveAttribute("target", "_blank");
    // l'ancien libellé « Ouvrir le texte / PDF » devient « Article en ligne »
    const online = screen.getByRole("link", { name: "openArticle ↗" });
    expect(online).toHaveAttribute("href", "https://ft.example");
    const links = screen.getAllByRole("link").map((l) => l.textContent);
    expect(links.indexOf("PDF ↗")).toBeLessThan(links.indexOf("DOI ↗"));
  });

  it("no pdfHref → no PDF link (P-3)", () => {
    render(
      <ArticleLinks article={{ doi: "10.1/x", doi_url: "https://doi.org/10.1/x" }} pdfHref={null} />
    );
    expect(screen.queryByRole("link", { name: "PDF ↗" })).toBeNull();
  });

  it("pdfHref alone → the PDF link still renders (P-3)", () => {
    render(<ArticleLinks article={{}} pdfHref="/api/v1/projects/p1/articles/a1/pdf" />);
    expect(screen.getByRole("link", { name: "PDF ↗" })).toBeInTheDocument();
  });
});
