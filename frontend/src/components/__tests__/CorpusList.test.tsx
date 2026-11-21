import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CorpusList } from "@/components/CorpusList";
import * as api from "@/lib/api";
import { Article } from "@/lib/api";

// i18n keys returned as-is (same convention as the articles page tests).
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  // rich interpolation (PdfDropzone panel): a function value is invoked (as the runtime
  // does) and its returned node is rendered after the key.
  t.rich = (key: string, params?: Record<string, unknown>) => {
    if (!params) return key;
    const values = Object.values(params).map((v) =>
      typeof v === "function" ? (v as (c: unknown) => unknown)(null) : v
    );
    return [key, ...values];
  };
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listArticles: jest.fn(),
  matchArticlePdf: jest.fn(),
}));

const listArticles = api.listArticles as jest.Mock;

const TITLE1 = "Surgical correction of ventricular septal defects in neonatal rats";
const TITLE2 = "Longitudinal MRI assessment of cortical development in preterm infants";

const A1: Article = {
  id: "a1",
  title: TITLE1,
  first_author: "Lambert, M.",
  journal: "J Neurosurg Res",
  pub_date: "2024-03-12",
  origin: "pubmed",
  doi: "10.1/x",
  doi_url: "https://doi.org/10.1/x",
  has_fulltext: true,
} as unknown as Article;
const A2: Article = {
  id: "a2",
  title: TITLE2,
  first_author: null,
  journal: null,
  pub_date: null,
  origin: "references",
  has_fulltext: false,
} as unknown as Article;

function mount(overrides: {
  isLead?: boolean;
  fulltextCount?: number | null;
  total?: number;
  items?: Article[];
} = {}) {
  listArticles.mockReset();
  listArticles.mockResolvedValue({
    items: overrides.items ?? [A1, A2],
    total: overrides.total ?? 2,
    page: 1,
    per_page: 50,
    stats: null,
  });
  render(
    <CorpusList
      projectId="p1"
      isLead={overrides.isLead ?? true}
      articleCount={overrides.total ?? 2}
      fulltextCount={overrides.fulltextCount ?? 0}
    />
  );
}

// spec 2026-11-20-corpus-view R2/R5.
describe("CorpusList", () => {
  it("renders the title, the counter and the rows", async () => {
    mount();
    expect(await screen.findByText("corpusTitle")).toBeInTheDocument();
    expect(screen.getByText("corpusCount")).toBeInTheDocument();
    // row 1: truncated title (title attr = full title), authors, journal · date, origin, fulltext
    const row1 = (await screen.findByText(TITLE1)).closest("li");
    expect(row1).toHaveTextContent("Lambert, M. et al.");
    expect(row1).toHaveTextContent("J Neurosurg Res");
    expect(screen.getByText("corpusOrigin_pubmed")).toBeInTheDocument();
    expect(row1).toHaveTextContent("✓ corpusFulltext");
    // row 2: no sub-line, the other origin, abstract only
    const row2 = screen.getByText(TITLE2).closest("li");
    expect(row2).toHaveTextContent("— corpusAbstractOnly");
    expect(screen.getByText("corpusOrigin_references")).toBeInTheDocument();
    // the API is called on the whole corpus: no search, page 1, 50 per page
    expect(listArticles).toHaveBeenCalledWith(
      "p1",
      1,
      50,
      undefined,
      "added",
      expect.anything(),
      undefined,
      undefined
    );
  });

  it("shows the fulltext part of the counter only when the count is available", async () => {
    mount({ total: 82, fulltextCount: 56 });
    expect(await screen.findByText("corpusCountFulltext")).toBeInTheDocument();
    expect(screen.queryByText("corpusCount")).not.toBeInTheDocument();
  });

  it("passes the quick search q to the API call (after the debounce)", async () => {
    mount();
    await screen.findByText(TITLE1);
    const input = screen.getByRole("searchbox");
    fireEvent.change(input, { target: { value: "mouse" } });
    await waitFor(() =>
      expect(listArticles).toHaveBeenCalledWith(
        "p1",
        1,
        50,
        undefined,
        "added",
        expect.anything(),
        undefined,
        "mouse"
      )
    );
  });

  it("passes the selected sort to the API call", async () => {
    mount();
    await screen.findByText(TITLE1);
    const select = screen.getByRole("combobox", { name: "sortBy" });
    fireEvent.change(select, { target: { value: "date_desc" } });
    await waitFor(() =>
      expect(listArticles).toHaveBeenCalledWith(
        "p1",
        1,
        50,
        undefined,
        "date_desc",
        expect.anything(),
        undefined,
        undefined
      )
    );
  });

  it("paginates by 50: next fetches page 2, previous is disabled on page 1", async () => {
    mount({ total: 82 });
    await screen.findByText(TITLE1);
    // « 1–50 sur 82 »
    expect(screen.getByText("corpusPages")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "next" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "next" }));
    await waitFor(() =>
      expect(listArticles).toHaveBeenCalledWith(
        "p1",
        2,
        50,
        undefined,
        "added",
        expect.anything(),
        undefined,
        undefined
      )
    );
    expect(screen.getByRole("button", { name: "previous" })).toBeEnabled();
  });

  it("links a row to the existing article sheet, without any search param", async () => {
    mount();
    const link = await screen.findByRole("link", { name: new RegExp(TITLE1.slice(0, 30)) });
    expect(link).toHaveAttribute("href", "/projects/p1/annotate?article=a1");
    expect(link.getAttribute("href")).not.toContain("search=");
  });

  it("shows the « create an analysis » CTA for a lead only", async () => {
    mount({ isLead: true });
    expect(await screen.findByRole("link", { name: /corpusCtaAction/ })).toHaveAttribute(
      "href",
      "/projects/p1/inference"
    );
    expect(screen.getByText("corpusCta")).toBeInTheDocument();
  });

  it("hides the CTA for a non-lead", async () => {
    mount({ isLead: false });
    await screen.findByText(TITLE1);
    expect(screen.queryByText("corpusCta")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /corpusCtaAction/ })).not.toBeInTheDocument();
  });
});

// spec 2026-11-21-liste-depot-copie R6/R11: PDF drop zone on the corpus view + one-click copy.
describe("CorpusList — PDF drop zone & copy (spec 2026-11-21)", () => {
  it("lead → the list card is the drop zone; member → no drop zone", async () => {
    mount({ isLead: true });
    await screen.findByText(TITLE1);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    expect(input).toHaveAttribute("accept", ".pdf");
    // R11: each row offers the title copy button; the row with a DOI adds the DOI link + copy button
    expect(screen.getAllByRole("button", { name: "copyTitle" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "copyDoi" })).toBeInTheDocument();
  });

  it("member → no drop zone", async () => {
    mount({ isLead: false });
    await screen.findByText(TITLE1);
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });

  it("lead + drop of a PDF → the list is reloaded (load called)", async () => {
    (api.matchArticlePdf as jest.Mock).mockResolvedValue({ article_id: "a1", title: TITLE1, score: 0.9 });
    mount({ isLead: true });
    await screen.findByText(TITLE1);
    const before = listArticles.mock.calls.length;
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.drop(input.parentElement!, {
      dataTransfer: { types: ["Files"], files: [new File(["a.pdf"], "a.pdf", { type: "application/pdf" })] },
    });
    await waitFor(() => expect(listArticles.mock.calls.length).toBeGreaterThan(before));
    expect(api.matchArticlePdf).toHaveBeenCalledTimes(1);
  });
});
