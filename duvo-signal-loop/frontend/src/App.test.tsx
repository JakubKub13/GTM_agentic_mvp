import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import App from "./App";
import { runStreamUrl } from "./lib/api";

afterEach(() => vi.unstubAllGlobals());

/** Smoke test: App composes AuthGuard → LoginScreen when /me is 401 (unauthenticated). */
describe("app composition smoke", () => {
  it("shows the login screen when unauthenticated (GET /me -> 401)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "not authenticated" }),
      })),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/"]}>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    // LoginScreen renders the brand + a Google sign-in link to the backend OAuth start.
    expect(await screen.findByText("duvo signal loop")).toBeInTheDocument();
    const signIn = await screen.findByRole("link", { name: /sign in/i });
    expect(signIn).toHaveAttribute("href", "/auth/login");
  });

  it("builds a same-origin SSE stream URL (offline, no fetch)", () => {
    expect(runStreamUrl("abc 123")).toBe("/runs/abc%20123/stream");
  });
});
