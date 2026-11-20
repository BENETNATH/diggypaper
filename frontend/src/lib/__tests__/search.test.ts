import {
  articlesStoreKey,
  cleanSelection,
  currentSearch,
  isUntouched,
  nextAutoName,
  resolveSearchId,
  runStatusKey,
  selectionFromFilters,
  withSearch,
} from "@/lib/search";
import type { SearchInfo } from "@/lib/api";

describe("resolveSearchId", () => {
  it("the URL wins over the tab memory", () => {
    expect(resolveSearchId("a", "b")).toBe("a");
  });
  it("falls back to the memory, then to the initial search (undefined)", () => {
    expect(resolveSearchId(null, "b")).toBe("b");
    expect(resolveSearchId("", null)).toBeUndefined();
  });
});

describe("withSearch", () => {
  it("n'ajoute rien sans recherche", () => {
    expect(withSearch("/projects/1/articles")).toBe("/projects/1/articles");
  });
  it("adds or replaces search, keeping the other parameters", () => {
    expect(withSearch("/p/annotate?article=x&queue=list", "s1")).toBe("/p/annotate?article=x&queue=list&search=s1");
    expect(withSearch("/p/articles?search=old", "s2")).toBe("/p/articles?search=s2");
  });
});

describe("selection", () => {
  it("cleanSelection drops the empty criteria", () => {
    expect(cleanSelection({ answers: { a: "", b: "true" }, reviewed: undefined, sample_n: 0, seed: 3 })).toEqual({
      answers: { b: "true" },
    });
  });
  it("garde la graine seulement avec un tirage", () => {
    expect(cleanSelection({ sample_n: 40, seed: 7 })).toEqual({ sample_n: 40, seed: 7 });
  });
  it("selectionFromFilters: no filter = same batch as the search", () => {
    expect(selectionFromFilters("ALL", {}, "s1")).toEqual({ from_search_id: "s1" });
    expect(selectionFromFilters("ALL", {})).toEqual({});
  });
  it("selectionFromFilters keeps the full text / abstract only filter", () => {
    expect(selectionFromFilters("ALL", {}, "s1", undefined, true)).toEqual({ from_search_id: "s1", has_fulltext: true });
    expect(selectionFromFilters("ALL", {}, "s1", undefined, false)).toEqual({ from_search_id: "s1", has_fulltext: false });
  });
  it("selectionFromFilters keeps status and answers", () => {
    expect(selectionFromFilters("AUTO_ACCEPTED", { x: "true", y: "" }, "s1")).toEqual({
      from_search_id: "s1",
      status: "AUTO_ACCEPTED",
      answers: { x: "true" },
    });
  });
});

describe("recherches", () => {
  const s = (id: string, is_default: boolean) => ({ id, is_default }) as SearchInfo;
  it("currentSearch: the one requested, otherwise the initial one", () => {
    const all = [s("a", true), s("b", false)];
    expect(currentSearch(all, "b")?.id).toBe("b");
    expect(currentSearch(all)?.id).toBe("a");
    expect(currentSearch(null)).toBeUndefined();
  });
  it("currentSearch: no selection and no default (new projects, R9) → the first corpus, never nothing", () => {
    // the backend sorts default-first, then by date: the first item is the primary corpus
    expect(currentSearch([s("a", false), s("b", false)])?.id).toBe("a");
    expect(currentSearch([s("a", false)])?.id).toBe("a");
  });
  it("currentSearch: a stale requested id falls back to the first corpus (the selection is cleaned separately)", () => {
    expect(currentSearch([s("a", false), s("b", false)], "dead-id")?.id).toBe("a");
  });
  it("articlesStoreKey separates the filters of each search", () => {
    expect(articlesStoreKey("p")).toBe("diggy:articles:p");
    expect(articlesStoreKey("p", "s")).toBe("diggy:articles:p:s");
  });

  it("the quick search goes into the selection (cleaned) and into the filters", () => {
    expect(selectionFromFilters("ALL", {}, "s1", "  deep gait ")).toEqual({ from_search_id: "s1", q: "deep gait" });
    expect(selectionFromFilters("ALL", {}, "s1", "   ")).toEqual({ from_search_id: "s1" });
  });
});

describe("nextAutoName (spec 2026-11-20-analyses-runs-2 R3)", () => {
  const day = new Date("2026-11-20T00:00:00Z");
  it("next free rank for today's YYMMDD prefix", () => {
    expect(nextAutoName([], day)).toBe("261120-1");
    expect(nextAutoName(["261120-1"], day)).toBe("261120-2");
    expect(nextAutoName(["261120-1", "261120-2", "261120-4"], day)).toBe("261120-3");
  });
  it("runs of other days do not block today's rank", () => {
    expect(nextAutoName(["261101-1", "261101-2"], day)).toBe("261120-1");
    expect(nextAutoName(["261120-1", "261101-2"], day)).toBe("261120-2");
  });
  it("free-form names never block an auto rank", () => {
    expect(nextAutoName(["ma recherche", null], day)).toBe("261120-1");
  });
});

describe("runStatusKey (spec 2026-11-20-analyses-runs-2 R3)", () => {
  it("a cancelled run (FAILED + error=cancelled) reads « Annulée », not « Échec »", () => {
    expect(runStatusKey({ status: "FAILED", error: "cancelled" })).toBe("CANCELLED");
  });
  it("other runs keep their own status", () => {
    expect(runStatusKey({ status: "FAILED", error: "boom" })).toBe("FAILED");
    expect(runStatusKey({ status: "COMPLETED", error: null })).toBe("COMPLETED");
    expect(runStatusKey({ status: "PENDING", error: null })).toBe("PENDING");
  });
});

describe("isUntouched", () => {
  const base = { is_default: true, predicted: 0, reviewed: 0, last_job: null } as SearchInfo;
  it("initial search without analysis or review: hidden", () => {
    expect(isUntouched(base)).toBe(true);
  });
  it("as soon as an analysis, a review or a task exists, or if it is not the initial one: visible", () => {
    expect(isUntouched({ ...base, predicted: 1 })).toBe(false);
    expect(isUntouched({ ...base, reviewed: 1 })).toBe(false);
    expect(isUntouched({ ...base, last_job: { status: "FAILED", model: "m" } })).toBe(false);
    expect(isUntouched({ ...base, is_default: false })).toBe(false);
  });
});
