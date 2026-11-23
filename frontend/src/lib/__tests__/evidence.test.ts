import { splitEvidence } from "@/lib/evidence";

// spec 2026-11-23 P-5: evidence_quotes may be a flat list (legacy) or a
// dict per field (new). Both shapes must feed the same highlight list.
describe("splitEvidence", () => {
  it("flat list → empty byField, all = the list", () => {
    expect(splitEvidence(["a", "b"])).toEqual({ byField: {}, all: ["a", "b"] });
  });

  it("dict → byField as-is, all = the union (deduped, order kept)", () => {
    expect(splitEvidence({ design: ["q1", "q2"], blinded: ["q2", "q3"] })).toEqual({
      byField: { design: ["q1", "q2"], blinded: ["q2", "q3"] },
      all: ["q1", "q2", "q3"],
    });
  });

  it("anything else → empty (null, string, dict with non-list values)", () => {
    expect(splitEvidence(null)).toEqual({ byField: {}, all: [] });
    expect(splitEvidence("oops")).toEqual({ byField: {}, all: [] });
    expect(splitEvidence({ a: "not-a-list" })).toEqual({ byField: {}, all: [] });
  });

  it("non-string entries are dropped", () => {
    expect(splitEvidence({ a: ["ok", 42], b: null })).toEqual({ byField: { a: ["ok"] }, all: ["ok"] });
  });
});
