import { render, screen } from "@testing-library/react";
import { AnalysisRow } from "@/app/projects/[id]/inference/page";
import { SearchInfo } from "@/lib/api";

// i18n keys returned as-is (with their params): the test does not depend on the message files.
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(":")}` : key,
}));

const search: SearchInfo = {
  id: "s1",
  name: "Question A",
  kind: "standard",
  is_default: false,
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
  last_job: { status: "COMPLETED", model: "qwen3:8b", model_version: "v42" },
  last_benchmark: null,
};

describe("AnalysisRow", () => {
  it("shows the model and version of the last analysis (ModelRef)", () => {
    const { container } = render(<AnalysisRow s={search} projectId="p1" locale="fr" />);
    expect(container.textContent).toContain("qwen3:8b");
    expect(container.textContent).toContain("v42");
  });

  // spec 2026-11-02-legacy-questions §2: no ghost question
  it("question null: the noQuestion label, no ghost question text or title", () => {
    render(<AnalysisRow s={{ ...search, question: null }} projectId="p1" locale="fr" />);
    const p = screen.getByText("noQuestion");
    expect(p).toBeInTheDocument();
    expect(p).not.toHaveAttribute("title");
  });

  // spec 2026-11-02-legacy-questions §3: real progress, always shown
  it("blind search: reviewed/lot progress", () => {
    const { container } = render(
      <AnalysisRow
        s={{ ...search, kind: "blind", reviewed: 3, predicted: 0 }}
        projectId="p1"
        locale="fr"
      />
    );
    expect(container.textContent).toContain("progressReviewed:3:10");
    expect(container.textContent).not.toContain("progressAnalysed");
  });

  it("standard search: predicted/lot progress (including 0)", () => {
    const { container } = render(<AnalysisRow s={search} projectId="p1" locale="fr" />);
    expect(container.textContent).toContain("progressAnalysed:0:10");
    expect(container.textContent).not.toContain("progressReviewed");
  });

  // spec 2026-11-02-frontend-vocabulary-a11y-polish §3: polling-updated status is announced
  it("meta line (progress) carries aria-live=polite", () => {
    const { container } = render(<AnalysisRow s={search} projectId="p1" locale="fr" />);
    const meta = Array.from(container.querySelectorAll("p")).find((p) => p.textContent?.includes("progressAnalysed"));
    expect(meta).toBeDefined();
    expect(meta).toHaveAttribute("aria-live", "polite");
  });

  // spec 2026-11-02-frontend-vocabulary-a11y-polish §4: to-launch variant
  it("toLaunch: badge \"À lancer\" + primary \"Lancer\" action (no \"Ouvrir\")", () => {
    const { container } = render(<AnalysisRow s={search} projectId="p1" locale="fr" toLaunch />);
    expect(screen.getByText("badgeToLaunch")).toBeInTheDocument();
    const launch = screen.getByRole("button", { name: "launch" });
    expect(launch.closest("a")).toHaveAttribute("href", "/projects/p1/inference?search=s1&new=1");
    expect(screen.queryByText("open")).not.toBeInTheDocument();
    expect(container.querySelector("a[href*='/articles']")).toBeNull();
  });

  it("model_version null: the \"version not recorded\" label", () => {
    const { container } = render(
      <AnalysisRow
        s={{ ...search, last_job: { status: "COMPLETED", model: "qwen3:8b", model_version: null } }}
        projectId="p1"
        locale="fr"
      />
    );
    expect(container.textContent).toContain("qwen3:8b");
    expect(screen.getByText("versionNotRecorded")).toBeInTheDocument();
  });
});
