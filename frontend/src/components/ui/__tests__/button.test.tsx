import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui/Button";

// audit antislop 2026-11-23 F-3/F-7: house Button success variant + mobile targets.
describe("Button (house variants, audit 2026-11-23)", () => {
  it("F-3: renders the success variant with the success background token", () => {
    render(<Button variant="success">Submit</Button>);
    const el = screen.getByRole("button", { name: "Submit" });
    expect(el.className).toContain("bg-success");
    expect(el.className).toContain("text-primary-foreground");
  });

  it("F-3: loading submits are locked (disabled + aria-busy, spinner rendered)", () => {
    render(<Button variant="success" loading>Submit</Button>);
    const el = screen.getByRole("button", { name: "Submit" });
    expect(el).toBeDisabled();
    expect(el).toHaveAttribute("aria-busy", "true");
    // the spinner span is aria-hidden
    expect(el.querySelector("[aria-hidden]")).not.toBeNull();
  });

  it("F-7: sm and md keep the ≥ 44 px mobile tap target in their class", () => {
    const { rerender } = render(<Button size="sm">S</Button>);
    expect(screen.getByRole("button", { name: "S" }).className).toContain("min-h-[44px]");
    rerender(<Button size="md">M</Button>);
    expect(screen.getByRole("button", { name: "M" }).className).toContain("min-h-[44px]");
    expect(screen.getByRole("button", { name: "M" }).className).toContain("sm:min-h-[40px]");
  });
});
