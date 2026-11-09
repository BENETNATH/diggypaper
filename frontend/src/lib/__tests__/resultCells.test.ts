// spec 2026-11-09-triage-seuils-fonds-champs R3/R4 — pure logic of the colored
// result cells (tone, corner icon, initials) and the human field state.
import {
  cellIcon,
  cellTone,
  deriveHumanFieldState,
  initials,
} from "@/lib/resultCells";

describe("deriveHumanFieldState (R4)", () => {
  const model = { f1: true, f2: "souris", f3: null };

  it("no human annotation → none", () => {
    expect(deriveHumanFieldState(model, null, "f1")).toBe("none");
    expect(deriveHumanFieldState(model, undefined, "f1")).toBe("none");
  });

  it("human did not answer the field → none", () => {
    expect(deriveHumanFieldState(model, {}, "f1")).toBe("none");
    expect(deriveHumanFieldState(model, { f1: null }, "f1")).toBe("none");
    expect(deriveHumanFieldState(model, { other: "x" }, "f1")).toBe("none");
  });

  it("human answer equal to the model's → validated (booleans, choices, texts)", () => {
    expect(deriveHumanFieldState(model, { f1: true }, "f1")).toBe("validated");
    expect(deriveHumanFieldState(model, { f2: "souris" }, "f2")).toBe("validated");
    expect(deriveHumanFieldState(model, { f1: true, f2: "souris" }, "f2")).toBe("validated");
  });

  it("human answer different from the model's → corrected", () => {
    expect(deriveHumanFieldState(model, { f1: false }, "f1")).toBe("corrected");
    expect(deriveHumanFieldState(model, { f2: "rat" }, "f2")).toBe("corrected");
    expect(deriveHumanFieldState(model, { f2: "" }, "f2")).toBe("corrected");
  });

  it("model did not answer, human did → corrected", () => {
    expect(deriveHumanFieldState(model, { f3: "oui" }, "f3")).toBe("corrected");
    expect(deriveHumanFieldState(null, { f1: true }, "f1")).toBe("corrected");
    expect(deriveHumanFieldState(undefined, { f1: true }, "f1")).toBe("corrected");
  });
});

describe("cellTone (R3)", () => {
  const base = {
    rowError: false,
    badgeOn: true,
    fieldConfidence: 0.9,
    answerPresent: true,
    accept: 0.85,
  };

  it("conf ≥ accept threshold → good", () => {
    expect(cellTone(base)).toBe("good");
    expect(cellTone({ ...base, fieldConfidence: 0.85, accept: 0.85 })).toBe("good"); // border
    expect(cellTone({ ...base, fieldConfidence: 0.7, accept: 0.7 })).toBe("good");
  });

  it("conf below threshold with an answer → warn", () => {
    expect(cellTone({ ...base, fieldConfidence: 0.4 })).toBe("warn");
    expect(cellTone({ ...base, fieldConfidence: 0.84, accept: 0.85 })).toBe("warn");
  });

  it("accept family disabled (null) → never good, warn when an answer is present", () => {
    expect(cellTone({ ...base, accept: null, fieldConfidence: 0.99 })).toBe("warn");
  });

  it("low confidence without an answer → neutral", () => {
    expect(cellTone({ ...base, fieldConfidence: 0.4, answerPresent: false })).toBe("neutral");
  });

  it("no field confidence (NULL, old predictions) → neutral (R2)", () => {
    expect(cellTone({ ...base, fieldConfidence: null })).toBe("neutral");
    expect(cellTone({ ...base, fieldConfidence: undefined })).toBe("neutral");
  });

  it("badge option off for the question → neutral", () => {
    expect(cellTone({ ...base, badgeOn: false, fieldConfidence: null })).toBe("neutral");
  });

  it("row in error → error (grey), whatever the rest", () => {
    expect(cellTone({ ...base, rowError: true })).toBe("error");
    expect(cellTone({ ...base, rowError: true, badgeOn: false, fieldConfidence: null })).toBe("error");
  });
});

describe("cellIcon (R4)", () => {
  it("error row → no icon", () => {
    expect(cellIcon({ rowError: true, modelValue: true, humanState: "validated" })).toBe("none");
  });

  it("human reviewed the field → validated / corrected", () => {
    expect(cellIcon({ rowError: false, modelValue: true, humanState: "validated" })).toBe("validated");
    expect(cellIcon({ rowError: false, modelValue: true, humanState: "corrected" })).toBe("corrected");
    expect(cellIcon({ rowError: false, modelValue: null, humanState: "corrected" })).toBe("corrected");
  });

  it("model answered, not re-read → unread (·)", () => {
    expect(cellIcon({ rowError: false, modelValue: true, humanState: "none" })).toBe("unread");
    expect(cellIcon({ rowError: false, modelValue: "souris", humanState: "none" })).toBe("unread");
  });

  it("empty cell (no model nor human value) → no icon", () => {
    expect(cellIcon({ rowError: false, modelValue: null, humanState: "none" })).toBe("none");
    expect(cellIcon({ rowError: false, modelValue: undefined, humanState: "none" })).toBe("none");
  });
});

describe("initials (R4 tooltip)", () => {
  it("first initials of up to two words, uppercased", () => {
    expect(initials("Benoît Petit")).toBe("BP");
    expect(initials("Marie Curie Paul")).toBe("MC");
    expect(initials("petit")).toBe("P");
  });

  it("empty / null / email → first letter or empty", () => {
    expect(initials("marie@x.fr")).toBe("M");
    expect(initials("")).toBe("");
    expect(initials(null)).toBe("");
    expect(initials(undefined)).toBe("");
  });
});
