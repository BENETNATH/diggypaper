import { render, screen } from "@testing-library/react";
import { BottomNav } from "@/components/BottomNav";
import { useAuth } from "@/lib/auth";

const mockState = { pathname: "/projects" };
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string) => key,
}));
jest.mock("next/navigation", () => ({ usePathname: () => mockState.pathname }));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/components/LanguageSwitcher", () => ({
  LanguageSwitcher: () => <div data-testid="language-switcher" />,
}));

const mockUseAuth = useAuth as unknown as jest.Mock;

// spec 2026-11-09-clarte-2 — R4: pas de Déconnexion, ≤ 4 items, aria-current
describe("BottomNav (spec clarte-2 R4)", () => {
  it("a logged-in researcher sees ≤ 4 items, no logout, aria-current on the active item", () => {
    mockState.pathname = "/projects";
    mockUseAuth.mockReturnValue({ user: { role: "RESEARCHER" }, loading: false });
    const { container } = render(<BottomNav />);
    const nav = container.querySelector("nav")!;
    // the nav items (the language switcher is not a link)
    const links = nav.querySelectorAll("a");
    expect(links.length).toBeLessThanOrEqual(4);
    // the logout is not an item of the bar (it lives on the profile page)
    expect(screen.queryByRole("link", { name: /logout/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /logout/ })).toBeNull();
    // the language switcher stays
    expect(screen.getByTestId("language-switcher")).toBeInTheDocument();
    // the active item is current
    expect(screen.getByRole("link", { name: "projects" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "profile" })).not.toHaveAttribute("aria-current");
  });

  it("an admin gets the 4th item « admin » (→ /admin/users), still no logout", () => {
    mockState.pathname = "/admin/users";
    mockUseAuth.mockReturnValue({ user: { role: "ADMIN" }, loading: false });
    const { container } = render(<BottomNav />);
    const nav = container.querySelector("nav")!;
    expect(nav.querySelectorAll("a")).toHaveLength(4);
    const admin = screen.getByRole("link", { name: "admin" });
    expect(admin).toHaveAttribute("href", "/admin/users");
    expect(admin).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("button", { name: /logout/ })).toBeNull();
  });
});
