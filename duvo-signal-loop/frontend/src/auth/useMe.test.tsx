import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { useMe } from "./useMe";
import { api, ApiError, type Me } from "@/lib/api";

function wrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe("useMe", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("resolves the authenticated user from GET /me", async () => {
    const me: Me = { email: "ada@duvo.dev", name: "Ada", can_real_run: true };
    vi.spyOn(api, "me").mockResolvedValue(me);

    const { result } = renderHook(() => useMe(), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(me);
  });

  it("does not retry a 401 (unauthenticated is a terminal state, not an error to hammer)", async () => {
    const spy = vi.spyOn(api, "me").mockRejectedValue(new ApiError(401, "Unauthorized"));

    const { result } = renderHook(() => useMe(), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
