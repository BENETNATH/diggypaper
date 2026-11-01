import { render, screen } from "@testing-library/react";
import { ModelRef } from "@/components/ModelRef";

// i18n key returned as-is: the test does not depend on the message files.
jest.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));

const DIGEST = "a1b2c3d4e5f6" + "0".repeat(52);

describe("ModelRef", () => {
  it("hides the identifier when it equals the name", () => {
    const { container } = render(<ModelRef name="qwen3:8b" identifier="qwen3:8b" version={null} />);
    expect(container.textContent).toBe("qwen3:8b · versionNotRecorded");
  });

  it("shows the identifier in parentheses when it differs from the name", () => {
    const { container } = render(<ModelRef name="Big" identifier="qwen3:8b" version={null} />);
    expect(container.textContent).toContain("Big (qwen3:8b)");
  });

  it('without a version: "version not recorded"', () => {
    render(<ModelRef name="Big" />);
    expect(screen.getByText("versionNotRecorded")).toBeInTheDocument();
  });

  it("digest: short form displayed, full value in the title, keyboard-focusable", () => {
    const { container } = render(<ModelRef name="Big" version={DIGEST} />);
    const abbr = container.querySelector("abbr") as HTMLElement;
    expect(abbr.textContent).toBe("a1b2c3d4e5f6");
    expect(abbr).toHaveAttribute("title", DIGEST);
    expect(abbr).toHaveAttribute("tabindex", "0");
  });

  it("short version: plain text, no abbr (nothing more to reveal)", () => {
    const { container } = render(<ModelRef name="GPT" version="gpt-4o-2024-08-06" />);
    expect(container.querySelector("abbr")).toBeNull();
    expect(container.textContent).toContain("gpt-4o-2024-08-06");
  });
});
