import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ProfilePage from "@/app/profile/page";
import { useAuth } from "@/lib/auth";
import { generateApiKey, revokeApiKey } from "@/lib/api";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  updateProfile: jest.fn(),
  changePassword: jest.fn(),
  exportMyData: jest.fn(),
  anonymizeMe: jest.fn(),
  generateApiKey: jest.fn(),
  revokeApiKey: jest.fn(),
}));

const KEY = "dgk_8fQ2mK7xWp4L9nR3tY6vZ0cB5dH1jSae";

type TestUser = {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
  language_preference: string;
  data_consent_given: boolean;
  can_create_projects: boolean;
  created_at: string;
  api_key_set: boolean;
  api_key_created_at: string | null;
};

// Mutable auth state: the "no key -> key active" transitions follow refresh().
// beforeEach always assigns a full user before any render.
const auth = {
  user: {} as TestUser,
  loading: false,
  refresh: jest.fn(),
  logout: jest.fn(),
};

beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: jest.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
  auth.user = {
    id: "u1",
    email: "u@lab.fr",
    full_name: null,
    role: "RESEARCHER",
    language_preference: "fr",
    data_consent_given: true,
    can_create_projects: false,
    created_at: "2026-01-01T00:00:00Z",
    api_key_set: false,
    api_key_created_at: null,
  };
  (useAuth as jest.Mock).mockReturnValue(auth);
});

describe("Profile — personal API key (spec 2026-11-21)", () => {
  it("no key: the section is rendered, generate visible, no revoke", () => {
    render(<ProfilePage />);
    expect(screen.getByText("apiTitle")).toBeInTheDocument();
    expect(screen.getByText("apiGenerate")).toBeInTheDocument();
    expect(screen.queryByText("apiRevoke")).not.toBeInTheDocument();
    expect(screen.queryByText("apiActive")).not.toBeInTheDocument();
  });

  it("generate: the modal shows the key; copy writes it to the clipboard", async () => {
    (generateApiKey as jest.Mock).mockResolvedValue({ api_key: KEY });
    render(<ProfilePage />);
    fireEvent.click(screen.getByText("apiGenerate"));
    expect(await screen.findByText(KEY)).toBeInTheDocument();
    expect(screen.getByText("apiGeneratedTitle")).toBeInTheDocument();
    expect(screen.getByText("apiGeneratedWarning")).toBeInTheDocument();
    fireEvent.click(screen.getByText("apiCopy"));
    await waitFor(() =>
      expect((navigator.clipboard.writeText as jest.Mock)).toHaveBeenCalledWith(KEY)
    );
    // the label flips once the clipboard promise settles (async state update)
    expect(await screen.findByText("apiCopied")).toBeInTheDocument();
    // closing the modal refreshes the user -> the card switches to "key active"
    fireEvent.click(screen.getByText("apiClose"));
    expect(auth.refresh).toHaveBeenCalled();
  });

  it("key active: Active badge, created date, revoke button", () => {
    auth.user = { ...auth.user, api_key_set: true, api_key_created_at: "2026-11-21T09:00:00Z" };
    render(<ProfilePage />);
    expect(screen.getByText("apiActive")).toBeInTheDocument();
    expect(screen.getByText("apiCreated")).toBeInTheDocument();
    expect(screen.getByText("apiRevoke")).toBeInTheDocument();
    expect(screen.queryByText("apiGenerate")).not.toBeInTheDocument();
  });

  it("revoke: confirmation modal, DELETE called, back to the no-key state", async () => {
    auth.user = { ...auth.user, api_key_set: true, api_key_created_at: "2026-11-21T09:00:00Z" };
    (revokeApiKey as jest.Mock).mockResolvedValue(undefined);
    const { rerender } = render(<ProfilePage />);
    fireEvent.click(screen.getByText("apiRevoke"));
    expect(screen.getByText("apiRevokeTitle")).toBeInTheDocument();
    // the confirm button is the 2nd occurrence (card button + modal footer)
    fireEvent.click(screen.getAllByText("apiRevoke").at(-1)!);
    await waitFor(() => expect(revokeApiKey).toHaveBeenCalled());
    expect(auth.refresh).toHaveBeenCalled();
    // after refresh the user no longer has a key -> back to the no-key state
    auth.user = { ...auth.user, api_key_set: false, api_key_created_at: null };
    rerender(<ProfilePage />);
    expect(screen.getByText("apiGenerate")).toBeInTheDocument();
    expect(screen.queryByText("apiRevoke")).not.toBeInTheDocument();
  });

  it("POST network error: inline error message, no crash", async () => {
    (generateApiKey as jest.Mock).mockRejectedValue(new Error("Failed to fetch"));
    render(<ProfilePage />);
    fireEvent.click(screen.getByText("apiGenerate"));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // the page is intact: the generate button is still there
    expect(screen.getByText("apiGenerate")).toBeInTheDocument();
  });
});
