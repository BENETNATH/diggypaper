import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { HScroll } from "@/components/HScroll";
import { ResultsTable } from "@/components/ResultsTable";
import { Article, SchemaField } from "@/lib/api";

// i18n keys returned as-is (with their params): the test does not depend on the message files.
jest.mock("next-intl", () => ({
  useLocale: () => "fr",
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(":")}` : key,
}));

// jsdom: no layout, no pointer capture, no PointerEvent class — stub them.
beforeAll(() => {
  if (!window.PointerEvent) {
    (window as unknown as { PointerEvent: typeof MouseEvent }).PointerEvent = MouseEvent;
  }
  Element.prototype.setPointerCapture = jest.fn();
  Element.prototype.releasePointerCapture = jest.fn();
});

type Dims = { sw?: number; cw?: number; tw?: number };

/**
 * Render HScroll, simulate an overflow on the scroller (jsdom reports 0 for
 * scrollWidth/clientWidth) via window resize, then expose the bar parts.
 */
async function setup(dims: Dims = {}) {
  const { sw = 1500, cw = 1000, tw = 900 } = dims;
  const { container } = render(<HScroll><div>content</div></HScroll>);
  const scroller = container.querySelector<HTMLElement>(".overflow-x-auto")!;
  Object.defineProperty(scroller, "scrollWidth", { configurable: true, get: () => sw });
  Object.defineProperty(scroller, "clientWidth", { configurable: true, get: () => cw });
  fireEvent(window, new Event("resize"));
  if (sw <= cw) {
    await waitFor(() => expect(within(container).queryByRole("scrollbar")).toBeNull());
    return { scroller, bar: null, track: null, thumb: null };
  }
  const bar = await within(container).findByRole("scrollbar");
  const track = bar.querySelector<HTMLElement>(".track")!;
  const thumb = track.firstElementChild as HTMLElement;
  Object.defineProperty(track, "clientWidth", { configurable: true, get: () => tw });
  track.getBoundingClientRect = () =>
    ({ x: 0, y: 0, top: 0, left: 0, width: tw, height: 8, right: tw, bottom: 8 }) as DOMRect;
  return { scroller, bar, track, thumb };
}

describe("HScroll — spec 2026-11-19-hscroll-float", () => {
  it("1. renders role=scrollbar when overflowing, wired to the scroller", async () => {
    const { scroller, bar } = await setup();
    expect(bar).toHaveAttribute("role", "scrollbar");
    expect(bar).toHaveAttribute("aria-orientation", "horizontal");
    expect(bar).toHaveAttribute("aria-controls", scroller.id);
    await waitFor(() => expect(bar).toHaveAttribute("aria-valuemax", "500"));
    expect(bar).toHaveAttribute("aria-valuenow", "0");
  });

  it("2. renders no bar when the content fits", async () => {
    const { bar } = await setup({ sw: 1000, cw: 1000 });
    expect(bar).toBeNull();
  });

  it("3. aria-valuenow + thumb transform follow scrollLeft", async () => {
    const { scroller, bar, thumb } = await setup();
    scroller.scrollLeft = 250;
    fireEvent(scroller, new Event("scroll"));
    // thumb = 600px on a 900px track → x = (250/500) × (900−600) = 150
    await waitFor(() => expect(thumb!.style.transform).toBe("translateX(150px)"));
    expect(bar!.getAttribute("aria-valuenow")).toBe("250");
    // Fond du pouce défini dans globals.css : Tailwind v3 ne compile pas
    // bg-muted-foreground/45 (modificateur d'opacité sur couleur var()) — régression
    // « pouce invisible » si cette classe disparaît.
    expect(thumb).toHaveClass("hscroll-thumb");
  });

  it("4. thumb width = visible fraction of the track, min 36px", async () => {
    const { scroller, thumb } = await setup(); // 900 × 1000/1500 = 600
    fireEvent(scroller, new Event("scroll")); // relayout with the track width defined in setup
    await waitFor(() => expect(thumb!.style.width).toBe("600px"));
    const narrow = await setup({ tw: 10 }); // 10 × 1000/1500 ≈ 6.7 → 36
    fireEvent(narrow.scroller, new Event("scroll"));
    await waitFor(() => expect(narrow.thumb!.style.width).toBe("36px"));
  });

  it("5. clicking the middle of the track jumps to scrollLeft ≈ max/2", async () => {
    const { scroller, track } = await setup();
    fireEvent.pointerDown(track!, { clientX: 450 });
    expect(scroller.scrollLeft).toBeCloseTo(250, 1);
    fireEvent.pointerUp(track!);
  });

  it("6. dragging the thumb moves the scroll (scale = maxScroll / track width)", async () => {
    const { scroller, bar, thumb } = await setup();
    fireEvent.pointerDown(thumb!, { clientX: 100 });
    fireEvent.pointerMove(bar!, { clientX: 200 });
    expect(scroller.scrollLeft).toBeCloseTo((500 / 900) * 100, 1); // ≈ 55.56, clamped
    fireEvent.pointerUp(bar!);
  });

  it("7. keyboard: arrows ±40 (clamped), Home/End, PageDown +clientWidth", async () => {
    const { scroller, bar } = await setup({ sw: 2500, cw: 1000 }); // max = 1500
    fireEvent.keyDown(bar!, { key: "ArrowRight" });
    expect(scroller.scrollLeft).toBe(40);
    fireEvent.keyDown(bar!, { key: "ArrowLeft" });
    expect(scroller.scrollLeft).toBe(0); // clamped ≥ 0
    expect(fireEvent.keyDown(bar!, { key: "PageDown" })).toBe(false); // preventDefault
    expect(scroller.scrollLeft).toBe(1000); // + clientWidth
    fireEvent.keyDown(bar!, { key: "End" });
    expect(scroller.scrollLeft).toBe(1500);
    fireEvent.keyDown(bar!, { key: "Home" });
    expect(scroller.scrollLeft).toBe(0);
  });

  it("8. aria-label is the common.hscrollLabel translation", async () => {
    const { bar } = await setup();
    expect(bar).toHaveAttribute("aria-label", "hscrollLabel"); // i18n mock returns keys as-is
  });

  it("9. ResultsTable renders the floating scrollbar when the table overflows", async () => {
    const FIELDS: SchemaField[] = [
      { key: "design", label: "Design in vivo ?", type: "boolean" },
      { key: "species", label: "Espèce", type: "choice", options: ["souris", "rat"] },
    ];
    const ARTICLE = {
      id: "a1",
      title: "A mouse study",
      doi: "10.1/x",
      has_fulltext: true,
      fulltext_source: "upload",
      origin: "upload",
      prediction_status: "AUTO_ACCEPTED",
      annotated: true,
      answers: [
        { key: "design", label: "Design in vivo ?", value: true, confidence: 0.88, source: "model" },
        { key: "species", label: "Espèce", value: "souris", confidence: 0.7, source: "model" },
      ],
    } as Article;
    const { container } = render(<ResultsTable articles={[ARTICLE]} fields={FIELDS} hrefFor={() => "/x"} />);
    const scroller = container.querySelector("table")!.parentElement as HTMLElement;
    expect(scroller.className).toContain("overflow-x-auto");
    Object.defineProperty(scroller, "scrollWidth", { configurable: true, get: () => 1500 });
    Object.defineProperty(scroller, "clientWidth", { configurable: true, get: () => 1000 });
    fireEvent(window, new Event("resize"));
    await screen.findByRole("scrollbar");
  });
});
