import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { useAccountDetail } from "./useAccountDetail";
import { api, type AccountDetail } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, getAccount: vi.fn() } };
});

const detail: AccountDetail = {
  account: {
    run_id: "r1",
    domain: "acme.com",
    company_name: "Acme",
    country: null,
    status: "done",
    score: 8,
    tier: "Tier 1",
    confidence: "high",
    needs_human_research: 0,
    signals_count: 2,
    signals_json: "[]",
    score_json: "{}",
    error: null,
    started_at: null,
    finished_at: null,
    agent_log_json: "[]",
  },
  diff: { changed: true, prev_score: 6, prev_tier: "Tier 1" },
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useAccountDetail", () => {
  beforeEach(() => {
    vi.mocked(api.getAccount).mockReset();
  });

  it("fetches the account detail for the run + domain", async () => {
    vi.mocked(api.getAccount).mockResolvedValue(detail);
    const { result } = renderHook(() => useAccountDetail("r1", "acme.com"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(detail);
    expect(api.getAccount).toHaveBeenCalledWith("r1", "acme.com");
  });

  it("does not fetch while disabled (no domain selected)", () => {
    vi.mocked(api.getAccount).mockResolvedValue(detail);
    renderHook(() => useAccountDetail("r1", null), { wrapper });
    expect(api.getAccount).not.toHaveBeenCalled();
  });

  it("surfaces the error state when the request fails", async () => {
    vi.mocked(api.getAccount).mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useAccountDetail("r1", "acme.com"), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
