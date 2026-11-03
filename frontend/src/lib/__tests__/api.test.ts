import { errorDetail, errorKey } from "@/lib/api";

describe("errorDetail", () => {
  it("keeps a string detail", () => {
    expect(errorDetail({ detail: "selection is empty" })).toBe("selection is empty");
  });

  it("joins the FastAPI 422 validation messages (detail is an array of {loc, msg})", () => {
    const body = {
      detail: [
        { loc: ["body", "fields"], msg: "Value error, at least one field is required", type: "value_error" },
        { loc: ["body", "sample_n"], msg: "Input should be a valid integer, not a string", type: "int_parsing" },
      ],
    };
    expect(errorDetail(body)).toBe(
      "Value error, at least one field is required; Input should be a valid integer, not a string"
    );
  });

  it("joins a plain string array", () => {
    expect(errorDetail({ detail: ["a", "b"] })).toBe("a; b");
  });

  it("falls back to the error key of the body", () => {
    expect(errorDetail({ error: "rate_limited" })).toBe("rate_limited");
  });

  it("returns undefined when the body carries no detail", () => {
    expect(errorDetail({})).toBeUndefined();
  });
});

describe("errorKey", () => {
  it("maps the backend rate-limit detail to a translated key", () => {
    expect(errorKey("rate_limited")).toBe("errors.rateLimited");
  });

  it("maps an unknown detail to null (the caller falls back to the raw string)", () => {
    expect(errorKey("something-else")).toBeNull();
  });
});
