import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { AuthGuard } from "./AuthGuard";
import { api, ApiError, type Me } from "@/lib/api";

function renderGuard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthGuard>
          <div>protected content</div>
        </AuthGuard>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AuthGuard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a loading state while /me is in flight (no flash of login)", () => {
    vi.spyOn(api, "me").mockReturnValue(new Promise<Me>(() => {}));

    renderGuard();

    expect(screen.getByRole("status", { name: /checking sign-in/i })).toBeInTheDocument();
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /sign in with google/i }),
    ).not.toBeInTheDocument();
  });

  it("renders the login screen when /me returns 401", async () => {
    vi.spyOn(api, "me").mockRejectedValue(new ApiError(401, "Unauthorized"));

    renderGuard();

    await waitFor(() =>
      expect(screen.getByRole("link", { name: /sign in with google/i })).toBeInTheDocument(),
    );
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
  });

  it("renders children once /me resolves to a user", async () => {
    vi.spyOn(api, "me").mockResolvedValue({ email: "ada@duvo.dev" });

    renderGuard();

    await waitFor(() => expect(screen.getByText("protected content")).toBeInTheDocument());
  });

  it("shows an error (not the login screen) when /me fails for a non-auth reason", async () => {
    vi.spyOn(api, "me").mockRejectedValue(new ApiError(500, "boom"));

    renderGuard();

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /sign in with google/i }),
    ).not.toBeInTheDocument();
  });
});
