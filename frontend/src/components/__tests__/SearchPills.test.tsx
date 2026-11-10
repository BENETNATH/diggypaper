import { render, screen, fireEvent } from "@testing-library/react";
import { SearchPills } from "@/components/SearchPills";
import type { SearchInfo } from "@/lib/api";

// Same fr-catalog mock as the other page tests (avoids loading the ESM next-intl build).
jest.mock("next-intl", () => ({
  useTranslations: (ns: string) => (key: string) => {
    const fr: Record<string, string> = { project: { pillNew: "Nouvelle" } }[ns] ?? {};
    return fr[key] ?? key;
  },
}));

const mk = (id: string, name: string, kind: SearchInfo["kind"] = "standard"): SearchInfo => ({
  id,
  name,
  kind,
  is_default: false,
  question: "q?",
  fields: [],
  text_sections: null,
  parent: null,
  parent_spec: null,
  created_at: null,
  created_by: null,
  lot_size: 10,
  predicted: 10,
  reviewed: 0,
  breakdown: {},
  to_review: 0,
  last_job: null,
  last_benchmark: null,
});

// spec v4 R6 — barre de pastilles des analyses standards
describe("SearchPills", () => {
  it("one pill per analysis, the active one is pressed + highlighted", () => {
    render(
      <SearchPills searches={[mk("a", "A"), mk("b", "B")]} activeId="b" onPick={jest.fn()} ariaLabel="Analyse active" />
    );
    const pa = screen.getByRole("button", { name: "A · A" });
    const pb = screen.getByRole("button", { name: "B · B" });
    expect(pa).toHaveAttribute("aria-pressed", "false");
    expect(pb).toHaveAttribute("aria-pressed", "true");
    expect(pb).toHaveClass("border-primary");
    expect(pa).not.toHaveClass("border-primary");
  });

  it("clicking a pill selects that analysis", () => {
    const onPick = jest.fn();
    render(
      <SearchPills searches={[mk("a", "A"), mk("b", "B")]} activeId="a" onPick={onPick} ariaLabel="Analyse active" />
    );
    fireEvent.click(screen.getByRole("button", { name: "B · B" }));
    expect(onPick).toHaveBeenCalledWith("b");
  });

  it("lead: the label, the status dots and the dashed « + Nouvelle » pill", () => {
    const untouched = { ...mk("b", "B"), predicted: 0 };
    render(
      <SearchPills
        searches={[mk("a", "A"), untouched]}
        activeId="a"
        onPick={jest.fn()}
        ariaLabel="Analyse active"
        label="ANALYSES"
        newHref="/projects/p1/inference?new=1"
      />
    );
    expect(screen.getByText("ANALYSES")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "+ Nouvelle" });
    expect(link).toHaveAttribute("href", "/projects/p1/inference?new=1");
    // the first pill (predicted=10) shows the green status dot, the untouched one the grey one
    const [dotGreen, dotGrey] = screen.getAllByTestId("dot");
    expect(dotGreen).toHaveClass("bg-success");
    expect(dotGrey).toHaveClass("bg-muted");
  });

  it("spec clarte-1 R1: all analyses in the row — standards first, off-chain with their kind badge", () => {
    render(
      <SearchPills
        searches={[mk("a", "A"), mk("b", "Blind", "blind"), mk("c", "Tuning", "tuning")]}
        activeId="a"
        onPick={jest.fn()}
        ariaLabel="Analyse active"
      />
    );
    const blind = screen.getByRole("button", { name: /Blind/ });
    const tuning = screen.getByRole("button", { name: /Tuning/ });
    expect(blind).toHaveAttribute("aria-pressed", "false");
    expect(blind).toHaveTextContent("kind_blind");
    expect(tuning).toHaveTextContent("kind_tuning");
    // standards first, then off-chain
    const texts = screen.getAllByRole("button").map((b) => b.textContent);
    expect(texts[0]).toContain("A");
    expect(texts[1]).toContain("Blind");
    expect(texts[2]).toContain("Tuning");
  });

  it("renders nothing when there is no standard analysis", () => {
    const { container } = render(
      <SearchPills searches={[]} activeId={undefined} onPick={jest.fn()} ariaLabel="Analyse active" />
    );
    expect(container).toBeEmptyDOMElement();
  });
});
