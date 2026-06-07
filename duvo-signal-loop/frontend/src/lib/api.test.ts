import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, companiesToCsv, type Company } from "./api";

/** Capture the most recent fetch call's (url, init) for assertions. */
function mockFetch(response: unknown, status = 200) {
  const fn = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    json: async () => response,
  }));
  vi.stubGlobal("fetch", fn as unknown as typeof fetch);
  return fn;
}

describe("api client contract (matches duvo/api/routes_runs.py)", () => {
  beforeEach(() => {
    // a readable double-submit CSRF cookie, as auth.py sets at login
    document.cookie = "duvo_csrf=tok-123";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "duvo_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("startRun POSTs multipart/form-data with a companies file + fields", async () => {
    const fetchFn = mockFetch({ run_id: "r1" });
    const companies: Company[] = [
      { name: "Acme", domain: "acme.com", country: "US", description: "Widgets" },
    ];

    const res = await api.startRun({
      dry_run: true,
      confirm: false,
      concurrency: 3,
      skip_done_today: false,
      test_email: "t@e.com",
      companies,
    });

    expect(res).toEqual({ run_id: "r1" });
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/runs");
    expect(init.method).toBe("POST");
    // multipart: body is FormData (NOT a JSON string), so no application/json header
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    expect(form.get("dry_run")).toBe("true");
    expect(form.get("confirm")).toBe("false");
    expect(form.get("concurrency")).toBe("3");
    expect(form.get("file")).toBeInstanceOf(Blob);
    // CSRF header echoes the cookie (#13)
    expect((init.headers as Record<string, string>)["x-csrf-token"]).toBe("tok-123");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("startRun omits the file when no companies are parsed (repo default, #19)", async () => {
    const fetchFn = mockFetch({ run_id: "r2" });
    await api.startRun({ dry_run: true, companies: null });
    const [, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    const form = init.body as FormData;
    expect(form.has("file")).toBe(false);
    expect(form.get("dry_run")).toBe("true");
  });

  it("listRuns unwraps the { runs: [...] } envelope", async () => {
    const fetchFn = mockFetch({ runs: [{ run_id: "a" }, { run_id: "b" }] });
    const runs = await api.listRuns();
    expect(runs.map((r) => r.run_id)).toEqual(["a", "b"]);
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit | undefined];
    expect(url).toBe("/runs");
    // GET requests do not send a CSRF header
    expect((init?.headers as Record<string, string>)?.["x-csrf-token"]).toBeUndefined();
  });

  it("companiesToCsv round-trips headers + quotes commas", () => {
    const csv = companiesToCsv([
      { name: "Acme, Inc", domain: "acme.com", country: "US", description: "a" },
    ]);
    expect(csv.split("\n")[0]).toBe("name,domain,country,description");
    expect(csv).toContain('"Acme, Inc"');
  });
});
