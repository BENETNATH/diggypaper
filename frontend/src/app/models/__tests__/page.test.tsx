import { render, screen } from "@testing-library/react";
import ModelsPage from "@/app/models/page";
import * as api from "@/lib/api";
import { useAuth } from "@/lib/auth";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn(), replace: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({ useAuth: jest.fn() }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  listModels: jest.fn().mockResolvedValue([]),
  listOllama: jest.fn().mockResolvedValue({ items: [], pulls: {} }),
  getModelCapabilities: jest.fn(),
  pullOllama: jest.fn(),
  registerOllama: jest.fn(),
  deleteModel: jest.fn(),
  testExternalModel: jest.fn(),
  testInstalledModel: jest.fn(),
  addExternalModel: jest.fn(),
}));

const caps = (mode: "docker" | "host" | "off"): api.ModelCapabilities => ({
  ollama: { mode, configured: mode !== "off", reachable: false },
  hardware: { cores: 3, ram_gb: 4, avx: true, avx2: true, avx512: false, gpu: false },
  recommendation: {
    profile: "small",
    slow_cpu: false,
    models: [{ tag: "qwen2.5:3b-instruct", ram_gb: 3, kind: "small", fits: true }],
  },
});

beforeEach(() => {
  (useAuth as jest.Mock).mockReturnValue({
    user: { role: "ADMIN", email: "a@b.c" },
    loading: false,
  });
});

// Ollama off (OLLAMA_MODE=off, e.g. a prod VM with API-only backends): the whole
// internal part (service state, hardware, recommendations, import) is noise —
// it must be hidden, leaving only the external API section.
describe("ModelsPage — internal section hidden when Ollama is off", () => {
  it("mode off: the internal section and its hardware block are not rendered", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("off"));
    render(<ModelsPage />);
    expect(await screen.findByText("addExternal")).toBeInTheDocument();
    expect(screen.queryByText("addInternal")).not.toBeInTheDocument();
    // the hardware / recommendation block lives in the internal section
    expect(screen.queryByText(/hwTitle/)).not.toBeInTheDocument();
    expect(screen.queryByText("qwen2.5:3b-instruct")).not.toBeInTheDocument();
  });

  it("mode docker: the internal section is still rendered", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("docker"));
    render(<ModelsPage />);
    expect(await screen.findByText("addInternal")).toBeInTheDocument();
    expect(screen.getByText("addExternal")).toBeInTheDocument();
  });

  it("mode off: the empty-state hint no longer mentions Ollama", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("off"));
    render(<ModelsPage />);
    expect(await screen.findByText("empty")).toBeInTheDocument();
    expect(screen.getByText("emptyHintOff")).toBeInTheDocument();
    expect(screen.queryByText("emptyHint")).not.toBeInTheDocument();
  });
});
