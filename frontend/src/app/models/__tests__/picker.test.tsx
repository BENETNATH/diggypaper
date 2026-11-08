import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ModelsPage from "@/app/models/page";
import * as api from "@/lib/api";
import { useAuth } from "@/lib/auth";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${JSON.stringify(params)}` : key;
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
  listExternalModels: jest.fn(),
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
    user: { role: "RESEARCHER", email: "r@b.c" },
    loading: false,
  });
  jest.clearAllMocks();
});

describe("ModelsPage — endpoint model picker (multi-select)", () => {
  it("fetches the endpoint models and creates one model per selection", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("off"));
    (api.listExternalModels as jest.Mock).mockResolvedValue({ ok: true, models: ["m-a", "m-b"] });
    render(<ModelsPage />);
    const url = await screen.findByPlaceholderText("https://api.openai.com/v1");
    fireEvent.change(url, { target: { value: "https://api.x.test/v1" } });

    fireEvent.click(screen.getByText("extList"));
    expect(await screen.findByText("m-a")).toBeInTheDocument();
    expect(screen.getByText("m-b")).toBeInTheDocument();

    // both are selected by default: uncheck m-b → only m-a is imported
    fireEvent.click(screen.getByText("m-b"));
    fireEvent.click(screen.getByText("extAdd"));
    expect(api.addExternalModel).toHaveBeenCalledTimes(1);
    expect(api.addExternalModel).toHaveBeenCalledWith({
      name: "m-a",
      base_url: "https://api.x.test/v1",
      model: "m-a",
      api_key: undefined,
    });
  });

  it("sends the api_key with the listing request", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("off"));
    (api.listExternalModels as jest.Mock).mockResolvedValue({ ok: true, models: ["m-a"] });
    const { container } = render(<ModelsPage />);
    const url = await screen.findByPlaceholderText("https://api.openai.com/v1");
    fireEvent.change(url, { target: { value: "https://api.x.test/v1" } });
    fireEvent.change(container.querySelector('input[type="password"]')!, {
      target: { value: "sekret" },
    });

    fireEvent.click(screen.getByText("extList"));
    await screen.findByText("m-a");
    expect(api.listExternalModels).toHaveBeenCalledWith({
      base_url: "https://api.x.test/v1",
      api_key: "sekret",
    });
  });

  it("an empty listing falls back to the manual name entry", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("off"));
    (api.listExternalModels as jest.Mock).mockResolvedValue({ ok: true, models: [] });
    render(<ModelsPage />);
    const url = await screen.findByPlaceholderText("https://api.openai.com/v1");
    fireEvent.change(url, { target: { value: "https://api.x.test/v1" } });

    fireEvent.click(screen.getByText("extList"));
    expect(await screen.findByText("extListEmpty")).toBeInTheDocument();
    // the manual submit stays disabled without a name + a model
    expect(screen.getByText("extAdd")).toBeDisabled();
  });

  it("a failed listing shows the sanitized error code", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("off"));
    (api.listExternalModels as jest.Mock).mockResolvedValue({ ok: false, error: "timeout" });
    render(<ModelsPage />);
    const url = await screen.findByPlaceholderText("https://api.openai.com/v1");
    fireEvent.change(url, { target: { value: "https://api.x.test/v1" } });

    fireEvent.click(screen.getByText("extList"));
    expect(await screen.findByText(/extListFail/)).toBeInTheDocument();
    expect(screen.queryByText("m-a")).not.toBeInTheDocument();
  });

  it("changing the endpoint invalidates the fetched list", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("off"));
    (api.listExternalModels as jest.Mock).mockResolvedValue({ ok: true, models: ["m-a"] });
    render(<ModelsPage />);
    const url = await screen.findByPlaceholderText("https://api.openai.com/v1");
    fireEvent.change(url, { target: { value: "https://api.x.test/v1" } });

    fireEvent.click(screen.getByText("extList"));
    await screen.findByText("m-a");
    fireEvent.change(url, { target: { value: "https://api.y.test/v1" } });
    expect(screen.queryByText("m-a")).not.toBeInTheDocument();
  });

  it("disables the submit while the picker import is in flight (no double-submit)", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("off"));
    (api.listExternalModels as jest.Mock).mockResolvedValue({ ok: true, models: ["m-a", "m-b"] });
    let resolveAdd: () => void = () => {};
    (api.addExternalModel as jest.Mock).mockImplementation(
      () => new Promise<void>((r) => (resolveAdd = r))
    );
    render(<ModelsPage />);
    const url = await screen.findByPlaceholderText("https://api.openai.com/v1");
    fireEvent.change(url, { target: { value: "https://api.x.test/v1" } });
    fireEvent.click(screen.getByText("extList"));
    await screen.findByText("m-a");

    fireEvent.click(screen.getByText("extAdd"));
    await waitFor(() => expect(api.addExternalModel).toHaveBeenCalledTimes(1));
    // in flight: the submit is disabled → a second click registers nothing
    expect(screen.getByText("extAdd")).toBeDisabled();
    fireEvent.click(screen.getByText("extAdd"));
    expect(api.addExternalModel).toHaveBeenCalledTimes(1);
    resolveAdd();
    await waitFor(() => expect(api.addExternalModel).toHaveBeenCalledTimes(2));
  });

  it("does not apply a listing that was in flight when the endpoint changed", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("off"));
    let resolveList: (v: { ok: boolean; models?: string[] }) => void = () => {};
    (api.listExternalModels as jest.Mock).mockImplementation(
      () => new Promise((r) => (resolveList = r))
    );
    render(<ModelsPage />);
    const url = await screen.findByPlaceholderText("https://api.openai.com/v1");
    fireEvent.change(url, { target: { value: "https://api.x.test/v1" } });
    fireEvent.click(screen.getByText("extList"));
    // while the listing is in flight, the user switches endpoint
    fireEvent.change(url, { target: { value: "https://api.y.test/v1" } });
    // the pending response (for x) settles: it must not populate the picker for y
    resolveList({ ok: true, models: ["m-x"] });
    await waitFor(() => expect(screen.getByText("extList")).toBeEnabled());
    expect(screen.queryByText("m-x")).not.toBeInTheDocument();
  });

  it("changing the api key invalidates the fetched list", async () => {
    (api.getModelCapabilities as jest.Mock).mockResolvedValue(caps("off"));
    (api.listExternalModels as jest.Mock).mockResolvedValue({ ok: true, models: ["m-a"] });
    const { container } = render(<ModelsPage />);
    const url = await screen.findByPlaceholderText("https://api.openai.com/v1");
    fireEvent.change(url, { target: { value: "https://api.x.test/v1" } });
    fireEvent.click(screen.getByText("extList"));
    await screen.findByText("m-a");
    const key = container.querySelector('input[type="password"]')!;
    fireEvent.change(key, { target: { value: "other-key" } });
    expect(screen.queryByText("m-a")).not.toBeInTheDocument();
  });
});
