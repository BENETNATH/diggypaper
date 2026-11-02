import { render, screen } from "@testing-library/react";
import { AnalysisRow } from "@/app/projects/[id]/inference/page";
import { SearchInfo } from "@/lib/api";

// i18n keys returned as-is: the test does not depend on the message files.
jest.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));

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
};

describe("AnalysisRow", () => {
  it("shows the model and version of the last analysis (ModelRef)", () => {
    const { container } = render(<AnalysisRow s={search} projectId="p1" locale="fr" />);
    expect(container.textContent).toContain("qwen3:8b");
    expect(container.textContent).toContain("v42");
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
