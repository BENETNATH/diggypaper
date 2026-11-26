import {
  ApiError,
  DEDUP_SETTLE_TIMEOUT_MS,
  deleteSearchJob,
  errorDetail,
  errorKey,
  getProject,
  listArticles,
  listSearchJobs,
  login,
  loginRedirectHref,
  me,
  rerunSearch,
  triggerInference,
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

  it("a 401 from a data call redirects to /login?next=<encoded url> and stays pending", async () => {
    // the exact URL assigned to window.location.href by doRequest on 401
    expect(loginRedirectHref("/projects/p1/inference", "?search=s1")).toBe(
      `/login?next=${encodeURIComponent("/projects/p1/inference?search=s1")}`
    );
    const original = global.fetch;
    global.fetch = unauthorized("Not authenticated");
    try {
      const p = getProject("p1");
      // the timeout sentinel wins only if the call never settles (no error raised)
      const outcome = await Promise.race([p, new Promise((r) => setTimeout(r, 25))]);
      expect(outcome).toBeUndefined();
    } finally {
      global.fetch = original;
    }
  });

  it("a 401 from /auth/me raises ApiError instead of redirecting (guest state, not session expired)", async () => {
    // AuthProvider calls /auth/me on every page (incl. public /register); a
    // 401 there means "no session", and redirecting would kick invitees off
    // /register?token=… before the form loads.
    const original = global.fetch;
    global.fetch = unauthorized("Not authenticated");
    try {
      await expect(me()).rejects.toThrow(ApiError);
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
      await expect(getProject("p2")).rejects.toThrow(ApiError);
    } finally {
      global.fetch = original;
      window.history.replaceState(null, "", "/");
    }
  });
});
// spec 2026-11-20-analyses-runs-2 F2: named runs, per-run results, run deletion.
describe("analyses-runs helpers (spec 2026-11-20-2 F2)", () => {
  const fetchOnce = (body: unknown, status = 200) => {
    const f = jest.fn(async () => ({
      ok: true,
      status,
      json: async () => body,
    })) as unknown as typeof fetch;
    return f;
  };
  const lastCall = (m: jest.Mock): [string, RequestInit | undefined] =>
    m.mock.calls[m.mock.calls.length - 1];

  it("listSearchJobs: GET /projects/{id}/searches/{sid}/jobs", async () => {
    const f = fetchOnce({ jobs: [] }) as unknown as jest.Mock;
    const original = global.fetch;
    global.fetch = f;
    try {
      const r = await listSearchJobs("p1", "s1");
      expect(r).toEqual({ jobs: [] });
      const [url, opts] = lastCall(f);
      expect(url).toBe("/api/v1/projects/p1/searches/s1/jobs");
      expect(opts?.method ?? "GET").toBe("GET");
    } finally {
      global.fetch = original;
    }
  });

  it("deleteSearchJob: DELETE /projects/{id}/searches/{sid}/jobs/{jid}", async () => {
    const f = fetchOnce(undefined, 204) as unknown as jest.Mock;
    const original = global.fetch;
    global.fetch = f;
    try {
      await deleteSearchJob("p1", "s1", "j1");
      const [url, opts] = lastCall(f);
      expect(url).toBe("/api/v1/projects/p1/searches/s1/jobs/j1");
      expect(opts?.method).toBe("DELETE");
    } finally {
      global.fetch = original;
    }
  });

  it("triggerInference with name: the name is sent, no overwrite_job_id", async () => {
    const f = fetchOnce({ job_id: "j1", status: "PENDING", name: "261120-1" }) as unknown as jest.Mock;
    const original = global.fetch;
    global.fetch = f;
    try {
      await triggerInference("p1", { model: "Kev", search_id: "s1", name: "261120-1" });
      const [url, opts] = lastCall(f);
      expect(url).toBe("/api/v1/projects/p1/inference");
      expect(JSON.parse(opts!.body as string)).toEqual({
        model: "Kev",
        search_id: "s1",
        name: "261120-1",
      });
    } finally {
      global.fetch = original;
    }
  });

  it("triggerInference in overwrite mode: overwrite_job_id is sent and NO name key", async () => {
    const f = fetchOnce({ job_id: "j2", status: "PENDING", name: "261120-1" }) as unknown as jest.Mock;
    const original = global.fetch;
    global.fetch = f;
    try {
      await triggerInference("p1", {
        model: "Kev",
        search_id: "s1",
        overwrite_job_id: "j1",
      });
      const body = JSON.parse(lastCall(f)[1]!.body as string);
      expect(body.overwrite_job_id).toBe("j1");
      expect(body).not.toHaveProperty("name");
    } finally {
      global.fetch = original;
    }
  });

  it("rerunSearch in overwrite mode: POST {model, overwrite_job_id}, no name", async () => {
    const f = fetchOnce({ job_id: "j2", status: "PENDING", name: "261120-1" }) as unknown as jest.Mock;
    const original = global.fetch;
    global.fetch = f;
    try {
      await rerunSearch("p1", "s1", "Kev", { overwrite_job_id: "j1" });
      const [url, opts] = lastCall(f);
      expect(url).toBe("/api/v1/projects/p1/searches/s1/rerun");
      expect(opts?.method).toBe("POST");
      expect(JSON.parse(opts!.body as string)).toEqual({ model: "Kev", overwrite_job_id: "j1" });
    } finally {
      global.fetch = original;
    }
  });

  it("listArticles with run: the ?run= query is appended", async () => {
    const f = fetchOnce({ items: [], total: 0, page: 1, per_page: 50, stats: {} }) as unknown as jest.Mock;
    const original = global.fetch;
    global.fetch = f;
    try {
      await listArticles("p1", 1, 50, undefined, "added", {}, "s1", undefined, undefined, "j1");
      const [url] = lastCall(f);
      expect(url).toContain("/api/v1/projects/p1/articles?");
      expect(url).toContain("search=s1");
      expect(url).toContain("run=j1");
    } finally {
      global.fetch = original;
    }
  });
});
