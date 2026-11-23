import { addQuote, cleanQuote, highlightSegments, quoteFound } from "@/lib/highlight";

describe("highlightSegments", () => {
  it("returns a single unhighlighted segment when there is no quote", () => {
    const segments = highlightSegments("Mice were randomized.", null);
    expect(segments).toEqual([{ text: "Mice were randomized.", highlighted: false }]);
  });

  it("surligne une citation exacte", () => {
    const segments = highlightSegments("Mice were randomized into groups.", ["randomized"]);
    expect(segments).toEqual([
      { text: "Mice were ", highlighted: false },
      { text: "randomized", highlighted: true, quoteIdx: [0] },
      { text: " into groups.", highlighted: false },
    ]);
  });

  it("is case-sensitive (verbatim extracts)", () => {
    // "Randomized" does not match "randomized" (different case)
    const segments = highlightSegments("Mice were Randomized.", ["randomized"]);
    expect(segments).toEqual([{ text: "Mice were Randomized.", highlighted: false }]);
    // but "Randomized" matches "Randomized"
    const segments2 = highlightSegments("Mice were Randomized.", ["Randomized"]);
    expect(segments2[1]).toEqual({ text: "Randomized", highlighted: true, quoteIdx: [0] });
  });

  it("surligne plusieurs citations distinctes", () => {
    const segments = highlightSegments("A and B and C.", ["A", "C"]);
    expect(segments).toEqual([
      { text: "A", highlighted: true, quoteIdx: [0] },
      { text: " and B and ", highlighted: false },
      { text: "C", highlighted: true, quoteIdx: [1] },
      { text: ".", highlighted: false },
    ]);
  });

  it("fusionne les citations chevauchantes", () => {
    const segments = highlightSegments("abcdef", ["abc", "cde"]);
    // abc (0-3) + cde (2-5) → fusion 0-5 = "abcde", les deux quotes portées
    expect(segments).toEqual([
      { text: "abcde", highlighted: true, quoteIdx: [0, 1] },
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
      { text: "text", highlighted: true, quoteIdx: [1] },
    ]);
  });

  it("ne surligne pas une citation absente", () => {
    const segments = highlightSegments("hello world", ["absent"]);
    expect(segments).toEqual([{ text: "hello world", highlighted: false }]);
  });
});

describe("matching normalisé (spec 2026-11-23 P-4)", () => {
  it("espace insécable : la quote sans insécable matche le texte", () => {
    const text = "Mice were\u00a0randomized into groups.";
    expect(quoteFound(text, "Mice were randomized")).toBe(true);
    const marked = highlightSegments(text, ["Mice were randomized"]).filter((s) => s.highlighted);
    expect(marked).toHaveLength(1);
    // le <mark> contient le texte ORIGINAL (avec l'insécable)
    expect(marked[0].text).toBe("Mice were\u00a0randomized");
    expect(marked[0].quoteIdx).toEqual([0]);
  });

  it("retour à la ligne dans la quote", () => {
    const text = "Mice were\nrandomized";
    expect(quoteFound(text, "Mice were randomized")).toBe(true);
    const marked = highlightSegments(text, ["Mice were randomized"]).filter((s) => s.highlighted);
    expect(marked[0].text).toBe("Mice were\nrandomized");
  });

  it("guillemets courbes, tirets et points de suspension", () => {
    expect(quoteFound("le “groupe A”", 'le "groupe A"')).toBe(true);
    expect(quoteFound("l’effet", "l'effet")).toBe(true);
    expect(quoteFound("a\u2013b\u2014c", "a-b-c")).toBe(true);
    expect(quoteFound("wait\u2026 then", "wait... then")).toBe(true);
  });

  it("ligature fi (U+FB01) matche la forme décomposée", () => {
    expect(quoteFound("the\ufb01rst group", "thefirst group")).toBe(true);
  });

  it("quote absente → pas de plage, quoteFound faux", () => {
    const segments = highlightSegments("hello world", ["absent"]);
    expect(segments).toEqual([{ text: "hello world", highlighted: false }]);
    expect(quoteFound("hello world", "absent")).toBe(false);
  });

  it("quoteIdx fusionnée sur une plage issue de deux quotes", () => {
    const segments = highlightSegments("abcdef", ["abc", "cde"]);
    expect(segments[0]).toEqual({ text: "abcde", highlighted: true, quoteIdx: [0, 1] });
  });

  it("la casse reste distinguée (extraits verbatim)", () => {
    expect(quoteFound("Mice were Randomized", "randomized")).toBe(false);
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
