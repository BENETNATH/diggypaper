import { errorKey } from "@/lib/api";

describe("errorKey", () => {
  it("maps the backend rate-limit detail to a translated key", () => {
    expect(errorKey("rate_limited")).toBe("errors.rateLimited");
  });

  it("maps an unknown detail to null (the caller falls back to the raw string)", () => {
    expect(errorKey("something-else")).toBeNull();
  });
});
