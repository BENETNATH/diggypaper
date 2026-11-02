import { render, screen } from "@testing-library/react";
import { RequireAdmin } from "@/components/RequireAdmin";

import { useAuth } from "@/lib/auth";

// i18n keys returned as-is: the test does not depend on the message files.
jest.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));

const mockUseAuth = useAuth as unknown as jest.Mock;

const auth = { user: null as unknown, loading: false };
beforeEach(() => {
  mockUseAuth.mockReturnValue(auth);
});

describe("RequireAdmin", () => {
  it("shows the translated loading state while the user is unknown", () => {
    auth.loading = true;
    render(<RequireAdmin>{<div>content</div>}</RequireAdmin>);
    expect(screen.getByText("loading")).toBeInTheDocument();
  });

  it("shows the translated admin-only message for a non-admin", () => {
    auth.loading = false;
    auth.user = { role: "RESEARCHER" };
    render(<RequireAdmin>{<div>content</div>}</RequireAdmin>);
    expect(screen.getByText("adminOnly")).toBeInTheDocument();
  });

  it("shows the content for an admin", () => {
    auth.loading = false;
    auth.user = { role: "ADMIN" };
    render(<RequireAdmin>{<div>content</div>}</RequireAdmin>);
    expect(screen.getByText("content")).toBeInTheDocument();
  });
});
