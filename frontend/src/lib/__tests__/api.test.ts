import {
  ApiError,
  DEDUP_SETTLE_TIMEOUT_MS,
  errorDetail,
  errorKey,
  getProject,
  login,
  loginRedirectHref,
  me,
} from "@/lib/api";

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

// audit 2026-11-10 P2-10 — identical GETs fired at the same time (layout + page on the
// same navigation) share the in-flight request instead of each hitting the backend.
describe("GET request coalescing (audit 2026-11-10 P2-10)", () => {
  const okJson = (body: unknown) =>
    jest.fn(async () => ({ ok: true, status: 200, json: async () => body }));

  it("two concurrent identical GETs trigger a single fetch", async () => {
    const fetchMock = okJson({ id: "p1" }) as unknown as jest.Mock;
    const original = global.fetch;
    global.fetch = fetchMock;
    try {
      const [a, b] = await Promise.all([getProject("p1"), getProject("p1")]);
      expect(a).toEqual(b);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally {
      global.fetch = original;
    }
  });

  it("sequential GETs are not coalesced (each re-fetches)", async () => {
    const fetchMock = okJson({ id: "p1" }) as unknown as jest.Mock;
    const original = global.fetch;
    global.fetch = fetchMock;
    try {
      await getProject("p1");
      await getProject("p1");
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally {
      global.fetch = original;
    }
  });

  // caveman-review 2026-11-10: a GET that never settles must not pin the key forever.
  it("a never-settling GET frees its dedup key after the settle timeout", async () => {
    jest.useFakeTimers();
    const original = global.fetch;
    const hanging = new Promise<Response>(() => {}); // network hang: never settles
    global.fetch = jest.fn(async () => hanging) as unknown as typeof fetch;
    try {
      void getProject("p1"); // in flight, will never settle
      jest.advanceTimersByTime(DEDUP_SETTLE_TIMEOUT_MS); // settle timeout fires → key dropped
      const fetchMock2 = okJson({ id: "p1" }) as unknown as jest.Mock;
      global.fetch = fetchMock2;
      await getProject("p1"); // a fresh request, NOT the hanging promise
      expect(fetchMock2).toHaveBeenCalledTimes(1);
    } finally {
      jest.useRealTimers();
      global.fetch = original;
    }
  });
});

// spec 2026-11-20 R6: a 401 from any call except /auth/login → hard redirect to
// /login?next=<current url>; the failing call stays pending (the page unmounts).
// jsdom cannot stub window.location (unforgeable in jsdom 26), so the redirect
// URL is asserted via loginRedirectHref (the value assigned to location.href)
// and the pending state via the call itself (no error raised).
describe("401 session expired → /login?next=", () => {
  const unauthorized = (detail: string) =>
    jest.fn(async () => ({
      ok: false,
      status: 401,
      statusText: "Unauthorized",
      json: async () => ({ detail }),
    })) as unknown as typeof fetch;

  it("a 401 from a non-login call redirects to /login?next=<encoded url> and stays pending", async () => {
    // the exact URL assigned to window.location.href by doRequest on 401
    expect(loginRedirectHref("/projects/p1/inference", "?search=s1")).toBe(
      `/login?next=${encodeURIComponent("/projects/p1/inference?search=s1")}`
    );
    const original = global.fetch;
    global.fetch = unauthorized("Not authenticated");
    try {
      const p = me();
      // the timeout sentinel wins only if the call never settles (no error raised)
      const outcome = await Promise.race([p, new Promise((r) => setTimeout(r, 25))]);
      expect(outcome).toBeUndefined();
    } finally {
      global.fetch = original;
    }
  });

  it("a 401 from /auth/login does not redirect (the ApiError is raised)", async () => {
    const original = global.fetch;
    global.fetch = unauthorized("Invalid or expired invitation token");
    try {
      await expect(login("a@b.c", "password-123")).rejects.toThrow(ApiError);
    } finally {
      global.fetch = original;
    }
  });

  it("a 401 while already on the /login page does not redirect (AuthProvider /auth/me loop guard)", async () => {
    const original = global.fetch;
    global.fetch = unauthorized("Not authenticated");
    try {
      window.history.replaceState(null, "", "/login");
      // different path than the previous test: the GET dedup map would otherwise
      // hand back its never-settling promise
      await expect(getProject("p1")).rejects.toThrow(ApiError);
    } finally {
      global.fetch = original;
      window.history.replaceState(null, "", "/");
    }
  });
});
