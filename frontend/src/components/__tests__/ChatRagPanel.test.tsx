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
  getVectorizeProgress: jest.fn(),
  triggerVectorize: jest.fn(),
  streamChat: jest.fn(),
  getResultsIndex: jest.fn(),
  triggerResultsIndex: jest.fn(),
}));

const getVectorizeProgress = api.getVectorizeProgress as jest.Mock;
const triggerVectorize = api.triggerVectorize as jest.Mock;
const streamChat = api.streamChat as jest.Mock;
const getResultsIndex = api.getResultsIndex as jest.Mock;
const triggerResultsIndex = api.triggerResultsIndex as jest.Mock;

beforeEach(() => {
  streamChat.mockReset();
  getResultsIndex.mockReset();
  triggerResultsIndex.mockReset();
});

/** A progress payload with a given ``indexed`` flag and an optional status. */
const progressOf = (indexed: boolean, status: api.VectorizeProgress["status"] = null) => ({
  status,
  total: 0,
  done: 0,
  model: null,
  started_at: null,
  finished_at: null,
  indexed,
});

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
  getVectorizeProgress.mockResolvedValue(progressOf(options.indexed ?? true));
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
  const answer = await screen.findByTestId("chat-assistant-turn");
  expect(answer.textContent).toBe("D'après [1] la cryo.");
});

test("highlights [n] markers and lists the cited sources under the answer", async () => {
  mockStream({
    tokens: ["D'après ", "[1] la cryo "],
    citations: [{ n: 1, article_id: "a1", title: "Zebrafish cryo", excerpt: "Cryoinjury was performed." }],
  });
  renderPanel();
  await submit("Quoi ?");
  const answer = await screen.findByTestId("chat-assistant-turn");
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
  await screen.findByTestId("index-not-indexed");
  expect((screen.getByTestId("chat-input") as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByTestId("index-button")).toBeTruthy();
  fireEvent.click(screen.getByTestId("index-button"));
  await waitFor(() => expect(triggerVectorize).toHaveBeenCalledWith("p1"));
  expect(screen.getByTestId("index-started")).toBeTruthy();
});

test("members without lead rights see no index button", async () => {
  mockStream({ indexed: false });
  renderPanel({ isLead: false });
  await screen.findByTestId("index-not-indexed");
  expect(screen.queryByTestId("index-button")).toBeNull();
});

test("clearing the conversation resets the state and sends a follow-up with history", async () => {
  mockStream({ tokens: ["r1 [1]"] });
  renderPanel();
  await submit("q1");
  await screen.findByTestId("chat-assistant-turn");

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

test("a completed exchange renders exactly once — the streaming bubble is cleared", async () => {
  mockStream({
    tokens: ["r1 [1]"],
    citations: [{ n: 1, article_id: "a1", title: "Zebrafish cryo", excerpt: "Cryoinjury." }],
  });
  renderPanel();
  await submit("q1");
  await screen.findByTestId("chat-assistant-turn");
  // The finished exchange lives in the history only: one user turn, one
  // assistant turn, no leftover streaming bubble (the bug showed the last
  // question + answer twice).
  expect(screen.getAllByTestId("chat-user-turn")).toHaveLength(1);
  expect(screen.getAllByTestId("chat-assistant-turn")).toHaveLength(1);
  expect(screen.queryByTestId("chat-current-answer")).toBeNull();
  // The citations travel with the committed answer.
  const cites = await screen.findByTestId("chat-citations");
  expect(cites.textContent).toContain("Zebrafish cryo");
});


// ---------------------------------------------------------------------------
// Périmètre de chat (spec 2026-11-08-ui-concordance-chat-recherche)
// ---------------------------------------------------------------------------

const SEARCHES = [
  {
    id: "s1",
    name: "Rich lot",
    kind: "standard",
    is_default: false,
    question: null,
    fields: [],
    text_sections: null,
    parent: null,
    parent_spec: null,
    created_at: null,
  },
] as unknown as api.SearchInfo[];

function renderPanelWithSearches(overrides: { searches?: api.SearchInfo[] } = {}) {
  return render(
    <ChatRagPanel
      projectId="p1"
      models={MODELS as never}
      isLead={false}
      searches={overrides.searches ?? SEARCHES}
    />
  );
}

test("scope selector defaults to corpus and sends no search_id", async () => {
  mockStream({ tokens: ["ok "] });
  renderPanelWithSearches();
  const scope = screen.getByTestId("chat-scope");
  expect(scope).toHaveValue("corpus");
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
});

test("selecting a search sends its search_id", async () => {
  mockStream({ tokens: ["ok "] });
  renderPanelWithSearches();
  fireEvent.change(screen.getByTestId("chat-scope"), { target: { value: "s1" } });
  await submit("Quoi ?");
  expect(streamChat).toHaveBeenCalledWith(
    "p1",
    { question: "Quoi ?", model: "Kev-4B", history: [], search_id: "s1" },
    expect.any(Function),
    expect.any(Function),
    expect.any(Function),
    expect.any(Function),
    expect.any(AbortSignal)
  );
});

test("changing scope resets the conversation", async () => {
  mockStream({ tokens: ["q1 "] });
  renderPanelWithSearches();
  await submit("q1");
  await screen.findByTestId("chat-assistant-turn");
  expect(screen.getByTestId("chat-user-turn")).toBeInTheDocument();
  fireEvent.change(screen.getByTestId("chat-scope"), { target: { value: "s1" } });
  expect(screen.queryByTestId("chat-user-turn")).toBeNull();
  expect(screen.queryByTestId("chat-assistant-turn")).toBeNull();
  expect(screen.getByTestId("chat-empty")).toBeInTheDocument();
});

test("no searches: no scope selector, existing behavior unchanged", async () => {
  mockStream({ tokens: ["ok "] });
  renderPanel();
  expect(screen.queryByTestId("chat-scope")).toBeNull();
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
});

// ---------------------------------------------------------------------------
// Mode résultats + mention « non exhaustif » (spec 2026-11-09-sous-analyse-criteres R5/R6)
// ---------------------------------------------------------------------------

const RESULTS_STATES: Record<string, api.ResultsIndexState> = {
  ready: { status: "ready", done: 1234, total: 1234 },
  indexing: { status: "indexing", done: 608, total: 1234 },
  failed: { status: "failed", done: 100, total: 1234 },
  not_started: { status: "not_started", done: 0, total: 0 },
  empty: { status: "empty", done: 0, total: 0 },
};

test("R6: the disclosure is permanent, mode-aware, and the tooltip is keyboard-accessible", async () => {
  mockStream({ tokens: ["ok "] });
  renderPanelWithSearches();
  const disclosure = screen.getByTestId("chat-disclosure");
  expect(disclosure.textContent).toContain("disclosureCorpus");
  // switch to results mode → the disclosure is mode-aware
  fireEvent.click(screen.getByTestId("chat-mode-results"));
  expect(screen.getByTestId("chat-disclosure").textContent).toContain("disclosureResults");
  // the ⓘ points to a live element with the tooltip content (aria-describedby)
  const info = screen.getByTestId("chat-info");
  const srId = info.getAttribute("aria-describedby");
  expect(srId).toBeTruthy();
  expect(document.getElementById(srId!)?.textContent).toContain("disclosureTooltip");
  expect(info).toHaveAttribute("tabindex", "0");
});

test("results mode sends mode + search_id in the request body", async () => {
  mockStream({ tokens: ["ok "] });
  getResultsIndex.mockResolvedValue(RESULTS_STATES.ready);
  renderPanelWithSearches();
  fireEvent.click(screen.getByTestId("chat-mode-results"));
  expect(screen.getByTestId("chat-scope")).toHaveValue("s1");
  // wait until the results index resolves to "ready" (input enables), so the
  // send() guard sees resultsReady=true deterministically.
  await waitFor(() => expect((screen.getByTestId("chat-input") as HTMLInputElement).disabled).toBe(false));
  await submit("Quoi ?");
  expect(streamChat).toHaveBeenCalledWith(
    "p1",
    { question: "Quoi ?", model: "Kev-4B", history: [], mode: "results", search_id: "s1" },
    expect.any(Function),
    expect.any(Function),
    expect.any(Function),
    expect.any(Function),
    expect.any(AbortSignal)
  );
});

test("corpus mode stays byte-identical: no mode key, corpus scope select intact", async () => {
  mockStream({ tokens: ["ok "] });
  renderPanelWithSearches();
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
  const scope = screen.getByTestId("chat-scope");
  expect(scope).toHaveValue("corpus");
  expect(screen.getByText("scopeCorpus")).toBeInTheDocument();
});

test("results mode, index in flight: explicit state, input disabled, no send", async () => {
  mockStream({ tokens: ["ok "] });
  getResultsIndex.mockResolvedValue(RESULTS_STATES.indexing);
  renderPanelWithSearches();
  fireEvent.click(screen.getByTestId("chat-mode-results"));
  const indexing = await screen.findByTestId("results-indexing");
  expect(indexing.textContent).toContain("resultsIndexing");
  expect(indexing.textContent).toContain("resultsIndexingProgress");
  expect((screen.getByTestId("chat-input") as HTMLInputElement).disabled).toBe(true);
  await submit("Quoi ?");
  expect(streamChat).not.toHaveBeenCalled();
});

test("results mode, failed: the lead retries (POST), the member sees no retry button", async () => {
  mockStream({ tokens: ["ok "] });
  getResultsIndex.mockResolvedValue(RESULTS_STATES.failed);
  triggerResultsIndex.mockResolvedValue(undefined);
  render(
    <ChatRagPanel projectId="p1" models={MODELS as never} isLead searches={SEARCHES} />
  );
  fireEvent.click(screen.getByTestId("chat-mode-results"));
  await screen.findByTestId("results-not-ready");
  const retry = screen.getByTestId("results-index-button");
  fireEvent.click(retry);
  await waitFor(() => expect(triggerResultsIndex).toHaveBeenCalledWith("p1", "s1"));
  expect((screen.getByTestId("chat-input") as HTMLInputElement).disabled).toBe(true);
});

test("results mode, failed, member: no retry button (the state stays explicit)", async () => {
  mockStream({ tokens: ["ok "] });
  getResultsIndex.mockResolvedValue(RESULTS_STATES.failed);
  renderPanelWithSearches();
  fireEvent.click(screen.getByTestId("chat-mode-results"));
  await screen.findByTestId("results-not-ready");
  expect(screen.queryByTestId("results-index-button")).toBeNull();
});

test("results mode, empty lot: explicit message, input disabled", async () => {
  mockStream({ tokens: ["ok "] });
  getResultsIndex.mockResolvedValue(RESULTS_STATES.empty);
  renderPanelWithSearches();
  fireEvent.click(screen.getByTestId("chat-mode-results"));
  await screen.findByTestId("results-empty");
  expect((screen.getByTestId("chat-input") as HTMLInputElement).disabled).toBe(true);
});

test("results mode, ready: the state block disappears and the chat works", async () => {
  mockStream({ tokens: ["ok "] });
  getResultsIndex.mockResolvedValue(RESULTS_STATES.ready);
  renderPanelWithSearches();
  fireEvent.click(screen.getByTestId("chat-mode-results"));
  await waitFor(() =>
    expect((screen.getByTestId("chat-input") as HTMLInputElement).disabled).toBe(false)
  );
  expect(screen.queryByTestId("results-indexing")).toBeNull();
  await submit("Quoi ?");
  expect(streamChat).toHaveBeenCalled();
});

test("the CTA request switches the panel to the results mode of that search", () => {
  mockStream({ tokens: ["ok "] });
  getResultsIndex.mockResolvedValue(RESULTS_STATES.ready);
  render(
    <ChatRagPanel
      projectId="p1"
      models={MODELS as never}
      isLead={false}
      searches={SEARCHES}
      resultsRequest={{ searchId: "s1", nonce: 1 }}
    />
  );
  expect(screen.getByTestId("chat-mode-results")).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByTestId("chat-scope")).toHaveValue("s1");
  expect(screen.getByTestId("chat-disclosure").textContent).toContain("disclosureResults");
});
