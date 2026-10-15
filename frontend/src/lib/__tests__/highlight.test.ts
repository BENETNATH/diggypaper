import { highlightSegments } from "@/lib/highlight";

describe("highlightSegments", () => {
  it("renvoie un seul segment non surligné si aucune citation", () => {
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

  it("est sensible à la casse (extraits verbatim)", () => {
    // "Randomized" ne matche pas "randomized" (casse différente)
    const segments = highlightSegments("Mice were Randomized.", ["randomized"]);
    expect(segments).toEqual([{ text: "Mice were Randomized.", highlighted: false }]);
    // mais "Randomized" matche "Randomized"
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

  it("gère les occurrences multiples d'une même citation", () => {
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
