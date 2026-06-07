import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { AppLayout } from "./AppLayout";
import * as nav from "@/auth/navigation";
import { ToastProvider } from "./Toast";
import { api, type Me } from "@/lib/api";

function renderLayout(me: Me = { email: "ada@duvo.dev", name: "Ada Lovelace" }) {
  vi.spyOn(api, "me").mockResolvedValue(me);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter>
          <AppLayout>
            <div>page body</div>
          </AppLayout>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("AppLayout", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the top nav, brand, and the routed page body", () => {
    renderLayout();
    expect(screen.getByRole("navigation")).toBeInTheDocument();
    expect(screen.getByText("page body")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /new run/i })).toHaveAttribute("href", "/");
  });

  it("shows the signed-in user in the menu trigger", async () => {
    renderLayout({ email: "ada@duvo.dev", name: "Ada Lovelace" });
    await waitFor(() => expect(screen.getByText(/ada lovelace/i)).toBeInTheDocument());
  });

  it("falls back to the email when no name is present", async () => {
    renderLayout({ email: "noname@duvo.dev" });
    await waitFor(() => expect(screen.getByText("noname@duvo.dev")).toBeInTheDocument());
  });

  it("opens the user menu and signs out, then redirects to the login start", async () => {
    const logout = vi.spyOn(api, "logout").mockResolvedValue(undefined);
    const redirect = vi.spyOn(nav, "hardRedirect").mockImplementation(() => {});
    renderLayout();
    await waitFor(() => expect(screen.getByText(/ada lovelace/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /account menu/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /sign out/i }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(redirect).toHaveBeenCalled());
  });

  it("closes the menu when Escape is pressed", async () => {
    renderLayout();
    await waitFor(() => expect(screen.getByText(/ada lovelace/i)).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /account menu/i }));
    expect(screen.getByRole("menuitem", { name: /sign out/i })).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("menuitem", { name: /sign out/i })).not.toBeInTheDocument(),
    );
  });
});
