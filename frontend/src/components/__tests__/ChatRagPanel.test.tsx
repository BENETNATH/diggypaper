import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import * as api from "@/lib/api";
import { ChatRagPanel } from "@/components/ChatRagPanel";

// i18n keys returned as-is: the tests do not depend on the message files.
jest.mock("next-intl", () => {
  const t = (key: string) => key;
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("@/lib/useSearch", () => ({ useSearchId: () => undefined }));
jest.mock("@/lib/search", () => ({ withSearch: (p: string) => p }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  semanticSearch: jest.fn(),
  triggerVectorize: jest.fn(),
  streamChat: jest.fn(),
}));

const semanticSearch = api.semanticSearch as jest.Mock;
const triggerVectorize = api.triggerVectorize as jest.Mock;
const streamChat = api.streamChat as jest.Mock;

const MODELS = [
  {
    id: "m1",
    name: "Kev-4B",
    backend_type: "OLLAMA",
    model_identifier: "kef/kev-4b",
    endpoint: null,
    ram_usage_mb: null,
    is_active: true,
    internal: true,
  },
  {
    id: "m2",
    name: "API-X",
    backend_type: "OPENAI_API",
    model_identifier: "https://x.test/v1#x",
    endpoint: "https://x.test",
    ram_usage_mb: null,
    is_active: true,
    internal: false,
  },
];

function renderPanel(overrides: { isLead?: boolean } = {}) {
  return render(
    <ChatRagPanel projectId="p1" models={MODELS as never} isLead={overrides.isLead ?? false} />
  );
}

async function submit(q: string) {
  const input = screen.getByTestId("chat-input");
  fireEvent.change(input, { target: { value: q } });
  await fireEvent.submit(input);
}

function mockStream(options: {
  indexed?: boolean;
  tokens?: string[];
  citations?: api.ChatCitation[];
  reject?: Error;
} = {}) {
  semanticSearch.mockResolvedValue({ results: [], indexed: options.indexed ?? true });
  if (options.reject) {
    streamChat.mockRejectedValue(options.reject);
    return;
  }
  streamChat.mockImplementation(
    (
      _p: string,
      _b: unknown,
      onToken?: (c: string) => void,
      onCitations?: (c: api.ChatCitation[]) => void,
      onDone?: (d: api.ChatDone) => void
    ) => {
      for (const tok of options.tokens ?? ["ok "]) onToken?.(tok);
      onCitations?.(options.citations ?? []);
      const full = (options.tokens ?? ["ok "]).join("");
      onDone?.({ full_text: full, model: "Kev-4B", n_citations: options.citations?.length ?? 0 });
      return Promise.resolve({ full_text: full, model: "Kev-4B", n_citations: 0 });
    }
  );
}

test("sends the question with the default Ollama model and an empty history", async () => {
  mockStream({ tokens: ["D'après ", "[1] la cryo."] });
  renderPanel();
  await submit("Quoi ?");
  expect(streamChat).toHaveBeenCalledWith(
    "p1",
    { question: "Quoi ?", model: "Kev-4B", history: [] },
    expect.any(Function),
    expect.any(Function),
    expect.any(Function),
    expect.any(Function),
    expect.any(AbortSignal)
  );
  const answer = await screen.findByTestId("chat-current-answer");
  expect(answer.textContent).toBe("D'après [1] la cryo.");
});

test("highlights [n] markers and lists the cited sources under the answer", async () => {
  mockStream({
    tokens: ["D'après ", "[1] la cryo "],
    citations: [{ n: 1, article_id: "a1", title: "Zebrafish cryo", excerpt: "Cryoinjury was performed." }],
  });
  renderPanel();
  await submit("Quoi ?");
  const answer = await screen.findByTestId("chat-current-answer");
  const mark = answer.querySelector("mark");
  expect(mark?.textContent).toBe("[1]");
  const cites = await screen.findByTestId("chat-citations");
  expect(cites.textContent).toContain("Zebrafish cryo");
  expect(cites.textContent).toContain("Cryoinjury was performed.");
  expect(cites.querySelector("a")?.getAttribute("href")).toBe("/projects/p1/annotate?article=a1");
});

test("model_busy rejection shows the localized message", async () => {
  mockStream({ reject: new api.ApiError(409, "model_busy — Ce modèle est occupé.") });
  renderPanel();
  await submit("Quoi ?");
  await screen.findByText("modelBusy");
  // No token was streamed before the rejection: the answer stays empty.
  expect(screen.getByTestId("chat-current-answer").textContent).toBe("");
});

test("unindexed corpus: input disabled, index button lead-only", async () => {
  mockStream({ indexed: false });
  renderPanel({ isLead: true });
  await screen.findByTestId("chat-not-indexed");
  expect((screen.getByTestId("chat-input") as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByTestId("chat-index-button")).toBeTruthy();
  fireEvent.click(screen.getByTestId("chat-index-button"));
  await waitFor(() => expect(triggerVectorize).toHaveBeenCalledWith("p1"));
  expect(screen.getByTestId("chat-index-started")).toBeTruthy();
});

test("members without lead rights see no index button", async () => {
  mockStream({ indexed: false });
  renderPanel({ isLead: false });
  await screen.findByTestId("chat-not-indexed");
  expect(screen.queryByTestId("chat-index-button")).toBeNull();
});

test("clearing the conversation resets the state and sends a follow-up with history", async () => {
  mockStream({ tokens: ["r1 [1]"] });
  renderPanel();
  await submit("q1");
  await screen.findByTestId("chat-current-answer");

  streamChat.mockClear();
  mockStream({ tokens: ["r2"] });
  await submit("q2");
  const turns = await screen.findAllByTestId("chat-assistant-turn");
  expect(turns[turns.length - 1].textContent).toBe("r2");
  const call = streamChat.mock.calls[0][1] as { history: { role: string; content: string }[] };
  expect(call.history).toEqual([
    { role: "user", content: "q1" },
    { role: "assistant", content: "r1 [1]" },
  ]);

  fireEvent.click(screen.getByText("clear"));
  await waitFor(() => expect(screen.queryByTestId("chat-empty")).toBeTruthy());
  expect(screen.queryByTestId("chat-current-answer")).toBeNull();
});
