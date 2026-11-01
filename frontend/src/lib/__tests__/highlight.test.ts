import { addQuote, cleanQuote, highlightSegments } from "@/lib/highlight";

describe("highlightSegments", () => {
  it("returns a single unhighlighted segment when there is no quote", () => {
    const segments = highlightSegments("Mice were randomized.", null);
    expect(segments).toEqual([{ text: "Mice were randomized.", highlighted: false }]);
  });

  it("surligne une citation exacte", () => {
    const segments = highlightSegments("Mice were randomized into groups.", ["randomized"]);
    expect(segments).toEqual([
      { text: "Mice were ", highlighted: false },
      { text: "randomized", highlighted: true },
      { text: " into groups.", highlighted: false },
    ]);
  });

  it("is case-sensitive (verbatim extracts)", () => {
    // "Randomized" does not match "randomized" (different case)
    const segments = highlightSegments("Mice were Randomized.", ["randomized"]);
    expect(segments).toEqual([{ text: "Mice were Randomized.", highlighted: false }]);
    // but "Randomized" matches "Randomized"
    const segments2 = highlightSegments("Mice were Randomized.", ["Randomized"]);
    expect(segments2[1]).toEqual({ text: "Randomized", highlighted: true });
  });

  it("surligne plusieurs citations distinctes", () => {
    const segments = highlightSegments("A and B and C.", ["A", "C"]);
    expect(segments).toEqual([
      { text: "A", highlighted: true },
      { text: " and B and ", highlighted: false },
      { text: "C", highlighted: true },
      { text: ".", highlighted: false },
    ]);
  });

  it("fusionne les citations chevauchantes", () => {
    const segments = highlightSegments("abcdef", ["abc", "cde"]);
    // abc (0-3) + cde (2-5) → fusion 0-5 = "abcde"
    expect(segments).toEqual([
      { text: "abcde", highlighted: true },
      { text: "f", highlighted: false },
    ]);
  });

  it("handles multiple occurrences of the same quote", () => {
    const segments = highlightSegments("cat cat cat", ["cat"]);
    expect(segments.filter((s) => s.highlighted)).toHaveLength(3);
  });

  it("renvoie un segment vide si le texte est vide", () => {
    expect(highlightSegments("", ["x"])).toEqual([{ text: "", highlighted: false }]);
  });

  it("ignore les citations vides", () => {
    const segments = highlightSegments("text", ["", "text"]);
    expect(segments).toEqual([
      { text: "text", highlighted: true },
    ]);
  });

  it("ne surligne pas une citation absente", () => {
    const segments = highlightSegments("hello world", ["absent"]);
    expect(segments).toEqual([{ text: "hello world", highlighted: false }]);
  });
});


describe("selection → evidence extract", () => {
  it("rogne et refuse un texte trop court", () => {
    expect(cleanQuote("  hello world \n")).toBe("hello world");
    expect(cleanQuote(" a ")).toBeNull();
    expect(cleanQuote("x".repeat(2000))?.length).toBe(1000);
  });
  it("adds without duplicate, and the added extract is highlighted in the text", () => {
    const quotes = addQuote(addQuote(["a b c"], "mice were"), "mice were");
    expect(quotes).toEqual(["a b c", "mice were"]);
    const segs = highlightSegments("Adult mice were housed", quotes);
    expect(segs.filter((s) => s.highlighted).map((s) => s.text)).toEqual(["mice were"]);
  });
});
