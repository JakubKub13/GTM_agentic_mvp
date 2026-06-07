import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";

import { useStartRun } from "./useStartRun";

const navigate = vi.fn();
vi.mock("react-router-dom", () => ({
  useNavigate: () => navigate,
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useStartRun", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    navigate.mockReset();
  });

  it("POSTs the request and navigates to the live run view on success", async () => {
    const startRun = vi
      .spyOn(api, "startRun")
      .mockResolvedValue({ run_id: "abc123" });

    const { result } = renderHook(() => useStartRun(), { wrapper });

    result.current.mutate({ dry_run: true });

    await waitFor(() => expect(startRun).toHaveBeenCalledTimes(1));
    expect(startRun).toHaveBeenCalledWith({ dry_run: true });
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/run/abc123"));
  });

  it("does not navigate on failure", async () => {
    vi.spyOn(api, "startRun").mockRejectedValue(new ApiError(403, "forbidden"));

    const { result } = renderHook(() => useStartRun(), { wrapper });

    result.current.mutate({ dry_run: false });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(navigate).not.toHaveBeenCalled();
  });
});
