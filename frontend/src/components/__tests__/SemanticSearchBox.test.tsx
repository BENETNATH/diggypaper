import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import * as api from "@/lib/api";
import { SemanticSearchBox } from "@/components/SemanticSearchBox";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("@/lib/useSearch", () => ({ useSearchId: () => undefined }));
jest.mock("@/lib/search", () => ({ withSearch: (p: string) => p }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  semanticSearch: jest.fn(),
  triggerVectorize: jest.fn(),
}));

const semanticSearch = api.semanticSearch as jest.Mock;
const triggerVectorize = api.triggerVectorize as jest.Mock;

const RESULTS = [
  {
    article: { id: "a1", title: "Zebrafish cryo", pmid: "1", doi: null },
    section_title: "Methods",
    excerpt: "Cryoinjury was performed on adult zebrafish hearts.",
    score: 0.032,
  },
  {
    article: { id: "a2", title: null, pmid: null, doi: "10.1/x" },
    section_title: null,
    excerpt: "Another section text.",
    score: 0.016,
  },
];

async function submit(query: string) {
  const input = screen.getByRole("searchbox");
  fireEvent.change(input, { target: { value: query } });
  await fireEvent.submit(input);
}

describe("SemanticSearchBox (§5)", () => {
  it("lists article, section, excerpt and score for a hybrid query", async () => {
    semanticSearch.mockResolvedValue({ results: RESULTS, indexed: true });
    render(<SemanticSearchBox projectId="p1" isLead={false} />);
    await submit("cryoinjury zebrafish");
    await waitFor(() => expect(semanticSearch).toHaveBeenCalledWith("p1", "cryoinjury zebrafish"));
    expect(screen.getByText("Zebrafish cryo")).toBeInTheDocument();
    expect(screen.getByText(/— Methods/)).toBeInTheDocument();
    expect(screen.getByText("Cryoinjury was performed on adult zebrafish hearts.")).toBeInTheDocument();
    // untitled article falls back to the i18n label
    expect(screen.getByText("untitled")).toBeInTheDocument();
  });

  it("shows the empty state when the query matches nothing", async () => {
    semanticSearch.mockResolvedValue({ results: [], indexed: true });
    render(<SemanticSearchBox projectId="p1" isLead={false} />);
    await submit("nothing at all");
    expect(await screen.findByText("empty")).toBeInTheDocument();
  });

  it("shows the not-indexed state and the lead-only index button", async () => {
    semanticSearch.mockResolvedValue({ results: [], indexed: false });
    const { rerender } = render(<SemanticSearchBox projectId="p1" isLead={false} />);
    await submit("cryo");
    expect(await screen.findByTestId("semantic-not-indexed")).toBeInTheDocument();
    expect(screen.queryByTestId("semantic-index-button")).toBeNull(); // not a lead

    rerender(<SemanticSearchBox projectId="p1" isLead={true} />);
    const btn = screen.getByTestId("semantic-index-button");
    expect(btn).toBeInTheDocument();
    triggerVectorize.mockResolvedValue({ task_id: "t1", queued: true });
    fireEvent.click(btn);
    await waitFor(() => expect(triggerVectorize).toHaveBeenCalledWith("p1"));
    expect(screen.getByTestId("semantic-index-started")).toBeInTheDocument();
  });

  it("surfaces API errors", async () => {
    semanticSearch.mockRejectedValue(new Error("boom"));
    render(<SemanticSearchBox projectId="p1" isLead={false} />);
    await submit("cryo");
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });
});
