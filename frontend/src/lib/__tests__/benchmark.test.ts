import { benchmarkErrorKey, tooManyCalls, estimateCalls, nextVariantName, pct, primaryParts, rankResults, relevanceOf, savedPromptVersion, showValue, toVariants, variantsProblem } from "@/lib/benchmark";
import type { BenchmarkResult } from "@/lib/api";

const res = (model: string, over: Partial<BenchmarkResult> & { primary?: number | null; agreement?: number | null } = {}): BenchmarkResult => ({
  model_name: model,
  variant: null,
  execution_time_seconds: over.execution_time_seconds ?? null,
  accuracy: null,
  precision_score: null,
  recall_score: null,
  f1_score: null,
  details: {
    ...(over.primary !== undefined ? { primary: { item: "relevance", metric: "recall" as const, value: over.primary } } : {}),
    ...(over.agreement !== undefined ? { agreement: over.agreement } : {}),
  },
});

describe("estimateCalls", () => {
  it("articles × modèles × variantes ; un modèle de décision compte 1", () => {
    expect(estimateCalls(30, [false, false], 3)).toBe(180);
    expect(estimateCalls(30, [false, true], 3)).toBe(120);
  });
  it("sans variante : un seul prompt", () => {
    expect(estimateCalls(10, [false, false], 0)).toBe(20);
    expect(estimateCalls(10, [], 2)).toBe(0);
  });
});

describe("rankResults", () => {
  it("classe sur le critère principal décroissant", () => {
    expect(rankResults([res("a", { primary: 0.5 }), res("b", { primary: 0.9 })]).map((r) => r.model_name)).toEqual(["b", "a"]);
  });
  it("à défaut de critère : accord décroissant, puis temps croissant", () => {
    const out = rankResults([
      res("lent", { agreement: 0.8, execution_time_seconds: 90 }),
      res("rapide", { agreement: 0.8, execution_time_seconds: 10 }),
      res("accord", { agreement: 0.95, execution_time_seconds: 200 }),
      res("rien"),
    ]);
    expect(out.map((r) => r.model_name)).toEqual(["accord", "rapide", "lent", "rien"]);
  });
  it("les résultats notés passent avant ceux sans critère, sans muter l'entrée", () => {
    const input = [res("x", { agreement: 1 }), res("y", { primary: 0 })];
    expect(rankResults(input).map((r) => r.model_name)).toEqual(["y", "x"]);
    expect(input[0].model_name).toBe("x");
  });
  it("accepte les anciens runs sans details", () => {
    const old = { model_name: "m", execution_time_seconds: 1, accuracy: 1, precision_score: 1, recall_score: 1, f1_score: 1 } as BenchmarkResult;
    expect(rankResults([old, old])).toHaveLength(2);
  });
});

describe("variantes", () => {
  it("nextVariantName évite les doublons", () => {
    expect(nextVariantName("Variante", ["Variante 2"])).toBe("Variante 3");
    expect(nextVariantName("Variante", [])).toBe("Variante 1");
  });
  it("variantsProblem : nom vide, doublon, prompt vide", () => {
    expect(variantsProblem([{ name: " ", system_prompt: "x" }])).toBe("name");
    expect(variantsProblem([{ name: "a", system_prompt: "x" }, { name: "a ", system_prompt: "y" }])).toBe("duplicate");
    expect(variantsProblem([{ name: "a", system_prompt: "  " }])).toBe("prompt");
    expect(variantsProblem([{ name: "a", system_prompt: "x" }])).toBeNull();
    expect(variantsProblem([])).toBeNull();
  });
  it("toVariants nettoie les noms et garde le prompt utilisateur s'il existe", () => {
    expect(toVariants([{ name: " a ", system_prompt: "s", user_prompt_schema: "u" }, { name: "b", system_prompt: "t" }])).toEqual([
      { name: "a", system_prompt: "s", user_prompt_schema: "u" },
      { name: "b", system_prompt: "t" },
    ]);
  });
  it("savedPromptVersion tient en 50 caractères", () => {
    expect(savedPromptVersion("12345678-aaaa", "x".repeat(80))).toHaveLength(50);
  });
});

describe("affichage", () => {
  it("pct / showValue / relevanceOf", () => {
    expect(pct(0.5)).toBe("50.0 %");
    expect(pct(null)).toBe("—");
    expect(showValue(true)).toBe("✓");
    expect(showValue(undefined)).toBe("—");
    expect(showValue("YES")).toBe("YES");
    expect(relevanceOf({ confidence_score: 0.876 })).toBe("88 %");
    expect(relevanceOf(null)).toBe("—");
  });
  it("primaryParts : libellé de l'item, null pour la pertinence", () => {
    const f = [{ key: "k", label: "Randomisé", type: "boolean" as const }];
    expect(primaryParts({ item: "k", metric: "f1", value: 1 }, f)).toEqual({ item: "Randomisé", metric: "f1" });
    expect(primaryParts({ item: "relevance", metric: "recall", value: 1 }, f)).toEqual({ item: null, metric: "recall" });
    expect(primaryParts(undefined, f)).toBeNull();
  });
});

describe("erreurs serveur", () => {
  it("reconnaît les messages connus", () => {
    expect(benchmarkErrorKey("no gold articles in the lot")).toBe("err_noGold");
    expect(benchmarkErrorKey("benchmark already in progress")).toBe("err_inProgress");
    expect(benchmarkErrorKey("autre chose")).toBeNull();
  });
  it("extrait le nombre d'appels", () => {
    expect(tooManyCalls("too many calls (4200 > 3000): use a smaller lot")).toBe(4200);
    expect(tooManyCalls("x")).toBeNull();
  });
});
