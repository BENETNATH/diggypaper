import { render, screen, fireEvent, act } from "@testing-library/react";
import { CopyButton } from "@/components/CopyButton";

// i18n keys returned as-is with their params (the tests do not depend on the message files).
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(":")}` : key,
}));

const writeText = (): jest.Mock => (navigator.clipboard as unknown as { writeText: jest.Mock }).writeText;

beforeEach(() => {
  Object.defineProperty(globalThis.navigator, "clipboard", {
    value: { writeText: jest.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
  jest.useFakeTimers();
});
afterEach(() => jest.useRealTimers());

describe("CopyButton (spec 2026-11-21 R8)", () => {
  it("click → clipboard.writeText with the value; aria-label stable", async () => {
    render(<CopyButton value="10.1016/0014-2999(78)90192-9" label="Copier le DOI" title="DOI" />);
    const btn = screen.getByRole("button", { name: "Copier le DOI" });
    expect(btn).toHaveAttribute("aria-label", "Copier le DOI");
    fireEvent.click(btn);
    await act(async () => {});
    expect(writeText()).toHaveBeenCalledTimes(1);
    expect(writeText()).toHaveBeenCalledWith("10.1016/0014-2999(78)90192-9");
  });

  it("success → « copié » tooltip for 2 s, then back to the copy icon", async () => {
    render(<CopyButton value="T" label="Copier le titre de l'article" title="Titre" />);
    fireEvent.click(screen.getByRole("button", { name: "Copier le titre de l'article" }));
    await act(async () => {});
    // tooltip + the aria-live region both carry the message
    expect(screen.getAllByText("copied:Titre")).toHaveLength(2);
    act(() => {
      jest.advanceTimersByTime(1999);
    });
    expect(screen.getAllByText("copied:Titre")).toHaveLength(2);
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(screen.queryAllByText("copied:Titre")).toHaveLength(0);
  });

  it("failure (clipboard rejected) → « Copie impossible » for 2 s", async () => {
    writeText().mockRejectedValueOnce(new Error("NotAllowedError"));
    render(<CopyButton value="T" label="Copier le titre de l'article" title="Titre" />);
    fireEvent.click(screen.getByRole("button", { name: "Copier le titre de l'article" }));
    await act(async () => {});
    expect(screen.getAllByText("bulkCopyFailed")).toHaveLength(2);
    act(() => {
      jest.advanceTimersByTime(2000);
    });
    expect(screen.queryAllByText("bulkCopyFailed")).toHaveLength(0);
  });
});
