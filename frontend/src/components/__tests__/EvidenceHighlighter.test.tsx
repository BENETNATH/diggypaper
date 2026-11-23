import fs from "node:fs";
import path from "node:path";
import { render } from "@testing-library/react";
import { EvidenceHighlighter } from "@/components/EvidenceHighlighter";

// i18n key returned as-is: the test does not depend on the message files.
jest.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));

const MARK_RECT = {
  top: 100,
  left: 0,
  right: 50,
  bottom: 110,
  width: 50,
  height: 10,
  x: 0,
  y: 100,
  toJSON: () => ({}),
} as DOMRect;

/** Synchronous requestAnimationFrame queue (flush by hand, bounded). */
function stubRaf() {
  const queue: Array<() => void> = [];
  const raf = (fn: FrameRequestCallback) => {
    queue.push(() => fn(0));
    return queue.length;
  };
  const caf = () => {
    queue.length = 0;
  };
  const flush = (n: number) => {
    for (let i = 0; i < n && queue.length; i++) queue.splice(0).forEach((f) => f());
  };
  return { raf, caf, flush };
}

describe("EvidenceHighlighter", () => {
  it("no hardcoded Tailwind highlight colors: theme tokens declared and consumed in globals.css", () => {
    const src = fs.readFileSync(path.join(__dirname, "../EvidenceHighlighter.tsx"), "utf8");
    expect(src).not.toContain("bg-yellow-200");
    expect(src).not.toContain("text-amber-900");
    const css = fs.readFileSync(path.join(__dirname, "../../app/globals.css"), "utf8");
    expect(css).toContain("--highlight-bg");
    expect(css).toContain("--highlight-fg");
    expect(css).toContain("var(--highlight-bg)");
    expect(css).toContain("var(--highlight-fg)");
  });

  it("prefers-reduced-motion: the scroll to the highlight is instant (behavior auto)", () => {
    const { raf, caf, flush } = stubRaf();
    const origRaf = global.requestAnimationFrame;
    const origCaf = global.cancelAnimationFrame;
    global.requestAnimationFrame = raf as unknown as typeof global.requestAnimationFrame;
    global.cancelAnimationFrame = caf as unknown as typeof global.cancelAnimationFrame;
    window.matchMedia = jest.fn().mockImplementation(() => ({
      matches: true,
      media: "(prefers-reduced-motion: reduce)",
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
    const scrollTo = jest.fn();
    const scrollBy = jest.fn();
    // jsdom: no scrollingElement — point it at the document element (the fallback scroller).
    Object.defineProperty(document, "scrollingElement", {
      configurable: true,
      get: () => document.documentElement,
    });
    (document.documentElement as unknown as HTMLElement).scrollTo = scrollTo;
    window.scrollBy = scrollBy as unknown as typeof window.scrollBy;

    render(<EvidenceHighlighter text="alpha beta gamma" quotes={["beta"]} focus={{ quote: "beta", n: 1 }} />);
    (document.querySelector("mark") as HTMLElement).getBoundingClientRect = () => MARK_RECT;
    flush(3);

    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(scrollTo.mock.calls[0][0]).toMatchObject({ behavior: "auto" });
    global.requestAnimationFrame = origRaf;
    global.cancelAnimationFrame = origCaf;
  });

  it("mark carries data-quote-idx (P-4)", () => {
    const { container } = render(<EvidenceHighlighter text="alpha beta gamma" quotes={["beta", "gamma"]} />);
    const marks = Array.from(container.querySelectorAll("mark"));
    expect(marks).toHaveLength(2);
    expect(marks[0].dataset.quoteIdx).toBe("0");
    expect(marks[1].dataset.quoteIdx).toBe("1");
  });

  it("absent quote → no mark at all (P-4)", () => {
    const { container } = render(<EvidenceHighlighter text="hello world" quotes={["absent"]} />);
    expect(container.querySelector("mark")).toBeNull();
  });

  it("scrolls to the mark of a normalized (non-verbatim) quote (P-4)", () => {
    const { raf, caf, flush } = stubRaf();
    const origRaf = global.requestAnimationFrame;
    const origCaf = global.cancelAnimationFrame;
    global.requestAnimationFrame = raf as unknown as typeof global.requestAnimationFrame;
    global.cancelAnimationFrame = caf as unknown as typeof global.cancelAnimationFrame;
    Object.defineProperty(document, "scrollingElement", {
      configurable: true,
      get: () => document.documentElement,
    });
    const scrollTo = jest.fn();
    (document.documentElement as unknown as HTMLElement).scrollTo = scrollTo;

    // le texte a un espace insécable, la quote (modèle) un simple espace
    const { container } = render(
      <EvidenceHighlighter text={"alpha beta\u00a0gamma"} quotes={["beta gamma"]} focus={{ quote: "beta gamma", n: 1 }} />
    );
    const mark = container.querySelector("mark") as HTMLElement;
    expect(mark).toBeTruthy();
    expect(mark.textContent).toBe("beta\u00a0gamma"); // le mark garde le texte original
    mark.getBoundingClientRect = () => MARK_RECT;
    flush(3);

    // le scroll ciblait ce mark (via data-quote-idx, plus le textContent)
    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(mark.classList.contains("evidence-flash")).toBe(true);
    global.requestAnimationFrame = origRaf;
    global.cancelAnimationFrame = origCaf;
  });
});
