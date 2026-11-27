import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import LoginPage from "@/app/login/page";
import { useAuth } from "@/lib/auth";
import { useRouter, useSearchParams } from "next/navigation";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => {
  const router = { push: jest.fn() };
  return { useRouter: () => router, useSearchParams: jest.fn() };
});
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getPublicConfig: jest.fn().mockResolvedValue({ privacy_url: null }),
}));

beforeEach(() => {
  (useAuth as jest.Mock).mockReturnValue({ login: jest.fn() });
  (useSearchParams as jest.Mock).mockReturnValue(new URLSearchParams(""));
});

// RGPD UX 2026-11-03: the consent checkbox must be visible BEFORE the first
// login attempt — a first-time user cannot know it is required until the
// server refuses (403), which is exactly what the checkbox is for.
describe("LoginPage — GDPR consent visibility", () => {
  it("the consent checkbox is shown from the start (before any login attempt)", () => {
    render(<LoginPage />);
    expect(screen.getByRole("checkbox")).toBeInTheDocument();
    expect(screen.getByText("consentFirstLogin")).toBeInTheDocument();
  });

  it("the consent hint stays (asked only once: the server remembers it)", async () => {
    render(<LoginPage />);
    // no privacy URL configured: the label does not cite an unreadable policy
    expect(await screen.findByText("consentLabelNoPolicy")).toBeInTheDocument();
  });
});

// spec 2026-11-20 R7: after a successful login, go back to ?next= (open-redirect
// guard: only a "/"-relative value is honoured, never "//evil.com").
describe("LoginPage — ?next= return after login", () => {
  async function submitLogin() {
    fireEvent.change(screen.getByLabelText("email"), { target: { value: "a@b.c" } });
    fireEvent.change(screen.getByLabelText("password"), { target: { value: "password-123" } });
    const form = screen.getByRole("button", { name: "loginButton" }).closest("form");
    fireEvent.submit(form!);
  }

  function successfulLogin() {
    (useAuth as jest.Mock).mockReturnValue({
      login: jest.fn().mockResolvedValue({ language_preference: null }),
    });
  }

  it("F3: returns to the ?next page after a successful login", async () => {
    (useSearchParams as jest.Mock).mockReturnValue(new URLSearchParams("next=/projects/1/inference"));
    successfulLogin();
    render(<LoginPage />);
    await submitLogin();
    await waitFor(() => expect(useRouter().push).toHaveBeenCalledWith("/projects/1/inference"));
  });

  it("F4: an external ?next (//evil.com) falls back to /projects (open-redirect guard)", async () => {
    (useSearchParams as jest.Mock).mockReturnValue(new URLSearchParams("next=//evil.com"));
    successfulLogin();
    render(<LoginPage />);
    await submitLogin();
    await waitFor(() => expect(useRouter().push).toHaveBeenCalledWith("/projects"));
    expect(useRouter().push).not.toHaveBeenCalledWith("//evil.com");
  });

  it.each([
    ["backslash", "/%5Cevil.com"],
    ["tab", "/%09/evil.com"],
  ])("F4b: a %s-obfuscated external ?next falls back to /projects", async (_name, raw) => {
    (useSearchParams as jest.Mock).mockReturnValue(new URLSearchParams(`next=${raw}`));
    successfulLogin();
    render(<LoginPage />);
    await submitLogin();
    await waitFor(() => expect(useRouter().push).toHaveBeenCalledWith("/projects"));
    expect(useRouter().push).not.toHaveBeenCalledWith(expect.stringContaining("evil"));
  });

  it("F5: without ?next, goes to /projects (existing behavior)", async () => {
    successfulLogin();
    render(<LoginPage />);
    await submitLogin();
    await waitFor(() => expect(useRouter().push).toHaveBeenCalledWith("/projects"));
  });
});
