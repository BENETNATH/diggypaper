import { render, screen } from "@testing-library/react";
import LoginPage from "@/app/login/page";
import { useAuth } from "@/lib/auth";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getPublicConfig: jest.fn().mockResolvedValue({ privacy_url: null }),
}));

beforeEach(() => {
  (useAuth as jest.Mock).mockReturnValue({ login: jest.fn() });
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
