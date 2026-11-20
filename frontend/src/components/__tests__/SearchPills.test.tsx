import { render, screen } from "@testing-library/react";
import { SearchPills } from "@/components/SearchPills";
import { SearchInfo } from "@/lib/api";

// i18n keys returned as-is (the tests do not depend on the message files).
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => ({
  Link: ({ children, href, ...rest }: { children: React.ReactNode; href: string } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const base: SearchInfo = {
  id: "s1",
  name: "Survie à 30 jours",
  kind: "standard",
  is_default: true,
  question: "q?",
  fields: [],
  text_sections: null,
  parent: null,
  parent_spec: null,
  created_at: null,
  created_by: null,
  lot_size: 10,
  predicted: 0,
  reviewed: 0,
  breakdown: {},
  to_review: 0,
  last_job: null,
  last_benchmark: null,
};

describe("SearchPills (spec 2026-11-20-2 §1, F1)", () => {
  it("no corpus: only the label and the CTA (no pills)", () => {
    render(
      <SearchPills
        searches={[]}
        activeId="s1"
        onPick={() => {}}
        ariaLabel="Corpus de questions"
        label="corporaTitle"
        newHref="/projects/p1/inference?new=1"
      />
    );
    expect(screen.getByText("corporaTitle")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /pillNew/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /pillNew/ })).toHaveAttribute("href", "/projects/p1/inference?new=1");
    // no pill button
    expect(screen.queryByRole("button", { name: /Survie à 30 jours/ })).not.toBeInTheDocument();
  });

  it("a corpus with results: green dot, no « brouillon »", () => {
    render(
      <SearchPills
        searches={[{ ...base, predicted: 3, last_job: { status: "COMPLETED", model: "Kev" } }]}
        activeId={undefined}
        onPick={() => {}}
        ariaLabel="Corpus de questions"
        label="corporaTitle"
      />
    );
    const pill = screen.getByRole("button", { name: /Survie à 30 jours/ });
    const dot = pill.querySelector('[data-testid="dot"]')!;
    expect(dot.className).toContain("bg-success");
    expect(pill.textContent).not.toContain("draft");
  });

  it("a corpus without any run: neutral dot + « brouillon » suffix", () => {
    render(
      <SearchPills
        searches={[{ ...base, predicted: 0, last_job: null }]}
        activeId={undefined}
        onPick={() => {}}
        ariaLabel="Corpus de questions"
        label="corporaTitle"
      />
    );
    const pill = screen.getByRole("button", { name: /Survie à 30 jours/ });
    const dot = pill.querySelector('[data-testid="dot"]')!;
    expect(dot.className).toContain("bg-muted");
    expect(pill.textContent).toContain("draft");
  });

  it("the active pill keeps the primary border pattern", () => {
    render(
      <SearchPills
        searches={[base]}
        activeId="s1"
        onPick={() => {}}
        ariaLabel="Corpus de questions"
        label="corporaTitle"
      />
    );
    const pill = screen.getByRole("button", { name: /Survie à 30 jours/ });
    expect(pill.className).toContain("border-primary");
    expect(pill).toHaveAttribute("aria-pressed", "true");
  });
});
