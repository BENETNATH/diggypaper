import { render, screen, fireEvent } from "@testing-library/react";
import { SearchPills } from "@/components/SearchPills";
import type { SearchInfo } from "@/lib/api";

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
    const pa = screen.getByRole("button", { name: "A" });
    const pb = screen.getByRole("button", { name: "B" });
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
    fireEvent.click(screen.getByRole("button", { name: "B" }));
    expect(onPick).toHaveBeenCalledWith("b");
  });

  it("renders nothing when there is no standard analysis", () => {
    const { container } = render(
      <SearchPills searches={[]} activeId={undefined} onPick={jest.fn()} ariaLabel="Analyse active" />
    );
    expect(container).toBeEmptyDOMElement();
  });
});
