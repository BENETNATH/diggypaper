import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PdfDropzone } from "@/components/PdfDropzone";
import * as api from "@/lib/api";

// i18n keys returned as-is with their params (the tests do not depend on the message files).
// Rich interpolation (t.rich, e.g. the matched title): a function value is invoked (as the
// runtime does) and its returned node is rendered after the key.
jest.mock("next-intl", () => {
  const t = (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(":")}` : key;
  t.rich = (key: string, params?: Record<string, unknown>) => {
    if (!params) return key;
    const values = Object.values(params).map((v) =>
      typeof v === "function" ? (v as (c: unknown) => unknown)(null) : v
    );
    return [key, ...values];
  };
  return { useLocale: () => "fr", useTranslations: () => t };
});
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  matchArticlePdf: jest.fn(),
}));

const matchArticlePdf = api.matchArticlePdf as jest.Mock;

const pdf = (name: string) => new File([name], name, { type: "application/pdf" });
const dt = (files: File[]) => ({ types: ["Files"], files });

beforeEach(() => matchArticlePdf.mockReset());

describe("PdfDropzone — overlay (spec 2026-11-21 R1)", () => {
  const zone = (container: HTMLElement) => container.firstChild as HTMLElement;

  it("dragenter with a PDF → active state; dragleave → back", () => {
    const { container } = render(
      <PdfDropzone projectId="p1" variant="overlay">
        <div>LISTE</div>
      </PdfDropzone>
    );
    const z = zone(container);
    fireEvent.dragEnter(z, { dataTransfer: dt([pdf("a.pdf")]) });
    expect(z.className).toContain("border-dashed");
    expect(screen.getByText("bulkDropTitle")).toBeInTheDocument();
    fireEvent.dragLeave(z, { dataTransfer: dt([pdf("a.pdf")]) });
    expect(z.className).not.toContain("border-dashed");
    expect(screen.queryByText("bulkDropTitle")).not.toBeInTheDocument();
  });

  it("dragenter without any PDF file → stays inactive", () => {
    const { container } = render(
      <PdfDropzone projectId="p1" variant="overlay">
        <div>LISTE</div>
      </PdfDropzone>
    );
    const z = zone(container);
    fireEvent.dragEnter(z, { dataTransfer: dt([new File(["x"], "notes.txt", { type: "text/plain" })]) });
    expect(z.className).not.toContain("border-dashed");
  });

  it("drop of 2 PDFs → 2 sequential matchArticlePdf calls, ok + nomatch statuses with the i18n texts", async () => {
    const order: string[] = [];
    let secondCallWhileFirstPending = false;
    matchArticlePdf.mockImplementation((_p: string, f: File) => {
      order.push(f.name);
      if (f.name === "a.pdf") {
        return new Promise((resolve) =>
          setTimeout(() => {
            secondCallWhileFirstPending = order.length > 1;
            resolve({ article_id: "x", title: "Titre A", score: 0.9 });
          }, 20)
        );
      }
      return Promise.reject(new Error("no_match"));
    });
    const onDone = jest.fn();
    const { container } = render(
      <PdfDropzone projectId="p1" variant="overlay" onDone={onDone}>
        <div>LISTE</div>
      </PdfDropzone>
    );
    fireEvent.drop(zone(container), { dataTransfer: dt([pdf("a.pdf"), pdf("b.pdf")]) });

    await screen.findByText("Titre A"); // the matched article title (rich interpolation in the real i18n)
    expect(screen.getByText(/bulkMatched/)).toBeInTheDocument();
    expect(screen.getByText(/bulkNoMatchLabel — bulkNoMatch/)).toBeInTheDocument();
    expect(screen.getByText("bulkPanel:2")).toBeInTheDocument();
    expect(matchArticlePdf).toHaveBeenCalledTimes(2);
    expect(order).toEqual(["a.pdf", "b.pdf"]);
    expect(secondCallWhileFirstPending).toBe(false); // sequential, no parallelism
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  });

  it("a non-PDF in the drop is ignored (no call, no panel)", () => {
    const { container } = render(
      <PdfDropzone projectId="p1" variant="overlay">
        <div>LISTE</div>
      </PdfDropzone>
    );
    fireEvent.drop(zone(container), {
      dataTransfer: dt([new File(["x"], "notes.txt", { type: "text/plain" })]),
    });
    expect(matchArticlePdf).not.toHaveBeenCalled();
    expect(screen.queryByText("bulkPanel:1")).not.toBeInTheDocument();
  });

  it("the × button closes the results panel", async () => {
    matchArticlePdf.mockResolvedValue({ article_id: "x", title: "T", score: 0.9 });
    const { container } = render(
      <PdfDropzone projectId="p1" variant="overlay">
        <div>LISTE</div>
      </PdfDropzone>
    );
    fireEvent.drop(zone(container), { dataTransfer: dt([pdf("a.pdf")]) });
    await screen.findByText("bulkPanel:1");
    fireEvent.click(screen.getByRole("button", { name: "close" }));
    expect(screen.queryByText("bulkPanel:1")).not.toBeInTheDocument();
  });
});

describe("PdfDropzone — inline (spec 2026-11-21 R5, BulkPdfUpload modal)", () => {
  it("renders the drop zone + hint, and the file pick button triggers one match call", async () => {
    matchArticlePdf.mockResolvedValue({ article_id: "x", title: "T", score: 0.9 });
    render(<PdfDropzone projectId="p1" variant="inline" />);
    expect(screen.getByText("bulkDropTitle")).toBeInTheDocument();
    expect(screen.getByText("bulkDropHint")).toBeInTheDocument();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toHaveAttribute("accept", ".pdf");
    Object.defineProperty(input, "files", { value: [pdf("a.pdf")], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await waitFor(() => expect(matchArticlePdf).toHaveBeenCalledTimes(1));
    expect(matchArticlePdf).toHaveBeenCalledWith("p1", expect.any(File));
    await screen.findByText("bulkPanel:1");
  });
});
