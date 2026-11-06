import { render, screen, waitFor } from "@testing-library/react";
import { DocumentUpload } from "@/components/DocumentUpload";

// i18n keys returned as-is: the test does not depend on the message files.
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(":")}` : key,
}));

// ---------------------------------------------------------------------------
// XMLHttpRequest mock — the component uploads and runs OCR through XHR.
// ---------------------------------------------------------------------------
type XhrSpec = { status: number; body?: Record<string, unknown> };

const state = {
  responses: [] as XhrSpec[],
  calls: [] as { url: string; method: string; body?: unknown }[],
};

class MockXHR {
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = {
    onprogress: null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  status = 0;
  responseText = "";
  url = "";
  method = "";
  headers: Record<string, string> = {};

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  async send(body?: unknown) {
    state.calls.push({ url: this.url, method: this.method, body });
    const spec = state.responses.shift() ?? { status: 200, body: {} };
    this.status = spec.status;
    this.responseText = JSON.stringify(spec.body ?? {});
    await Promise.resolve();
    // HTTP responses (4xx included) arrive via onload; onerror is network-only.
    this.onload?.();
  }
}
(globalThis as Record<string, unknown>).XMLHttpRequest = MockXHR;

beforeEach(() => {
  state.responses = [];
  state.calls = [];
});

function file(name: string, type: string) {
  return new File([name], name, { type });
}

function uploadResponse(body: Record<string, unknown>) {
  state.responses.push({ status: 200, body });
}

/** Queue the upload responses for the given files (the queue is processed in order). */
function queueUploads(specs: { name: string; body: Record<string, unknown> }[]) {
  for (const s of specs) uploadResponse(s.body);
}

const PROJECT = "p1";

async function upload(files: { name: string; body: Record<string, unknown> }[]) {
  render(<DocumentUpload projectId={PROJECT} />);
  // set the hidden input's files (jsdom has no DataTransfer: a plain array is enough —
  // the component only iterates the list)
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const list = files.map((f) => file(f.name, "application/pdf"));
  Object.defineProperty(input, "files", { value: list, configurable: true });
  input.dispatchEvent(new Event("change", { bubbles: true }));
  await waitFor(
    () => expect(state.calls.filter((c) => c.url.includes("/documents"))).toHaveLength(files.length),
    { timeout: 2000 }
  );
}

// spec 2026-11-06-ux-refonte-v4 R2
describe("DocumentUpload — OCR confirmed at upload (R2)", () => {
  it("shows the OCR panel (summary + scanned list + estimate) after a batch upload", async () => {
    queueUploads([
      { name: "contrat1.pdf", body: { id: "a1", needs_ocr: true, page_count: 3, duplicate: false } },
      { name: "contrat2.pdf", body: { id: "a2", needs_ocr: true, page_count: 7, duplicate: false } },
      { name: "notes.txt", body: { id: "a3", duplicate: false } },
    ]);
    await upload([
      { name: "contrat1.pdf", body: {} },
      { name: "contrat2.pdf", body: {} },
      { name: "notes.txt", body: {} },
    ]);

    // summary: 3 created, 1 with text, 2 scanned
    expect(await screen.findByText(/ocrSummary:/)).toBeInTheDocument();
    // the two scanned files are listed (the txt is not)
    expect(screen.getByText("contrat1.pdf")).toBeInTheDocument();
    expect(screen.getByText("contrat2.pdf")).toBeInTheDocument();
    // estimate = (3+7) pages × 1–3 s → 10–30 s
    const launch = screen.getByRole("button", { name: /ocrLaunch/ });
    expect(launch.textContent).toContain("ocrEstS:10:30");
    // the panel offers both paths: launch now, or keep the per-file badge
    expect(screen.getAllByText("scannedBadge")).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "runOcr" })).toHaveLength(2);
  });

  it("launches the existing per-article OCR endpoint sequentially, with progress", async () => {
    queueUploads([
      { name: "contrat1.pdf", body: { id: "a1", needs_ocr: true, page_count: 2, duplicate: false } },
      { name: "contrat2.pdf", body: { id: "a2", needs_ocr: true, page_count: 4, duplicate: false } },
    ]);
    await upload([
      { name: "contrat1.pdf", body: {} },
      { name: "contrat2.pdf", body: {} },
    ]);
    // OCR responses (200 with sections)
    state.responses.push({ status: 200, body: { sections: [] } });
    state.responses.push({ status: 200, body: { sections: [] } });

    screen.getByRole("button", { name: /ocrLaunch/ }).click();

    await waitFor(() => {
      const ocrCalls = state.calls.filter((c) => c.url.includes("/ocr"));
      expect(ocrCalls).toHaveLength(2);
      // sequential: first article, then second
      expect(ocrCalls[0].url).toBe(`/api/v1/projects/${PROJECT}/articles/a1/ocr`);
      expect(ocrCalls[1].url).toBe(`/api/v1/projects/${PROJECT}/articles/a2/ocr`);
      expect(ocrCalls.every((c) => c.method === "POST")).toBe(true);
    });
    await waitFor(() => expect(screen.getAllByText("ocrDone")).toHaveLength(2));
  });

  it("calls onSettled once the upload queue finishes, so the page can refresh", async () => {
    const onSettled = jest.fn();
    queueUploads([
      { name: "rapport1.pdf", body: { id: "b1", duplicate: false } },
      { name: "rapport2.txt", body: { id: "b2", duplicate: false } },
    ]);
    render(<DocumentUpload projectId={PROJECT} onSettled={onSettled} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const list = ["rapport1.pdf", "rapport2.txt"].map((n) => file(n, "application/pdf"));
    Object.defineProperty(input, "files", { value: list, configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));

    // not before both uploads are done…
    await waitFor(() =>
      expect(state.calls.filter((c) => c.url.includes("/documents"))).toHaveLength(2)
    );
    await waitFor(() => expect(onSettled).toHaveBeenCalled());
    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it("the per-file button runs OCR for that file only; unreadable text shows ocrEmpty", async () => {
    queueUploads([{ name: "scan.pdf", body: { id: "a9", needs_ocr: true, page_count: 1, duplicate: false } }]);
    await upload([{ name: "scan.pdf", body: {} }]);
    state.responses.push({ status: 422, body: { detail: "ocr_empty" } });

    screen.getByRole("button", { name: "runOcr" }).click();

    await waitFor(() => {
      const ocrCalls = state.calls.filter((c) => c.url.includes("/ocr"));
      expect(ocrCalls).toHaveLength(1);
      expect(ocrCalls[0].url).toBe(`/api/v1/projects/${PROJECT}/articles/a9/ocr`);
    });
    expect(await screen.findByText("ocrEmpty")).toBeInTheDocument();
  });
});
