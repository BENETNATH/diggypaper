import { articlesStoreKey, cleanSelection, currentSearch, isUntouched, resolveSearchId, selectionFromFilters, withSearch } from "@/lib/search";
import type { SearchInfo } from "@/lib/api";

describe("resolveSearchId", () => {
  it("l'URL l'emporte sur la mémoire de l'onglet", () => {
    expect(resolveSearchId("a", "b")).toBe("a");
  });
  it("retombe sur la mémoire, puis sur la recherche initiale (undefined)", () => {
    expect(resolveSearchId(null, "b")).toBe("b");
    expect(resolveSearchId("", null)).toBeUndefined();
  });
});

describe("withSearch", () => {
  it("n'ajoute rien sans recherche", () => {
    expect(withSearch("/projects/1/articles")).toBe("/projects/1/articles");
  });
  it("ajoute ou remplace search en gardant les autres paramètres", () => {
    expect(withSearch("/p/annotate?article=x&queue=list", "s1")).toBe("/p/annotate?article=x&queue=list&search=s1");
    expect(withSearch("/p/articles?search=old", "s2")).toBe("/p/articles?search=s2");
  });
});

describe("selection", () => {
  it("cleanSelection retire les critères vides", () => {
    expect(cleanSelection({ answers: { a: "", b: "true" }, reviewed: undefined, sample_n: 0, seed: 3 })).toEqual({
      answers: { b: "true" },
    });
  });
  it("garde la graine seulement avec un tirage", () => {
    expect(cleanSelection({ sample_n: 40, seed: 7 })).toEqual({ sample_n: 40, seed: 7 });
  });
  it("selectionFromFilters : sans filtre = même lot que la recherche", () => {
    expect(selectionFromFilters("ALL", {}, "s1")).toEqual({ from_search_id: "s1" });
    expect(selectionFromFilters("ALL", {})).toEqual({});
  });
  it("selectionFromFilters reprend le filtre texte intégral / résumé seul", () => {
    expect(selectionFromFilters("ALL", {}, "s1", undefined, true)).toEqual({ from_search_id: "s1", has_fulltext: true });
    expect(selectionFromFilters("ALL", {}, "s1", undefined, false)).toEqual({ from_search_id: "s1", has_fulltext: false });
  });
  it("selectionFromFilters reprend statut et réponses", () => {
    expect(selectionFromFilters("AUTO_ACCEPTED", { x: "true", y: "" }, "s1")).toEqual({
      from_search_id: "s1",
      status: "AUTO_ACCEPTED",
      answers: { x: "true" },
    });
  });
});

describe("recherches", () => {
  const s = (id: string, is_default: boolean) => ({ id, is_default }) as SearchInfo;
  it("currentSearch : demandée, sinon initiale", () => {
    const all = [s("a", true), s("b", false)];
    expect(currentSearch(all, "b")?.id).toBe("b");
    expect(currentSearch(all)?.id).toBe("a");
    expect(currentSearch(null)).toBeUndefined();
  });
  it("articlesStoreKey sépare les filtres de chaque recherche", () => {
    expect(articlesStoreKey("p")).toBe("diggy:articles:p");
    expect(articlesStoreKey("p", "s")).toBe("diggy:articles:p:s");
  });

  it("la recherche rapide entre dans la sélection (nettoyée) et dans les filtres", () => {
    expect(selectionFromFilters("ALL", {}, "s1", "  deep gait ")).toEqual({ from_search_id: "s1", q: "deep gait" });
    expect(selectionFromFilters("ALL", {}, "s1", "   ")).toEqual({ from_search_id: "s1" });
  });
});

describe("isUntouched", () => {
  const base = { is_default: true, predicted: 0, reviewed: 0, last_job: null } as SearchInfo;
  it("recherche initiale sans analyse ni relecture : masquée", () => {
    expect(isUntouched(base)).toBe(true);
  });
  it("dès qu'une analyse, une relecture ou une tâche existe, ou si ce n'est pas l'initiale : visible", () => {
    expect(isUntouched({ ...base, predicted: 1 })).toBe(false);
    expect(isUntouched({ ...base, reviewed: 1 })).toBe(false);
    expect(isUntouched({ ...base, last_job: { status: "FAILED", model: "m" } })).toBe(false);
    expect(isUntouched({ ...base, is_default: false })).toBe(false);
  });
});
