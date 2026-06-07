import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ToastProvider, useToast } from "./Toast";

/** A tiny consumer that lets a test drive the toast API via buttons. */
function Harness() {
  const { toast, dismiss } = useToast();
  return (
    <div>
      <button onClick={() => toast({ title: "Saved", variant: "success" })}>info</button>
      <button onClick={() => toast({ title: "Boom", description: "it broke", variant: "error" })}>
        err
      </button>
      <button
        onClick={() => toast({ title: "Sticky", duration: 0 })}
        data-testid="sticky"
      >
        sticky
      </button>
      <button onClick={() => dismiss()}>clear</button>
    </div>
  );
}

function renderHarness() {
  return render(
    <ToastProvider>
      <Harness />
    </ToastProvider>,
  );
}

describe("Toast (shell)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("renders a pushed toast and shows its title", async () => {
    renderHarness();
    await userEvent.click(screen.getByRole("button", { name: "info" }));
    expect(screen.getByText("Saved")).toBeInTheDocument();
  });

  it("marks error toasts as an assertive alert and others as polite status", async () => {
    renderHarness();
    await userEvent.click(screen.getByRole("button", { name: "err" }));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Boom");
    expect(alert).toHaveTextContent("it broke");
    expect(alert).toHaveAttribute("aria-live", "assertive");
  });

  it("dismisses a toast when its close button is clicked", async () => {
    renderHarness();
    await userEvent.click(screen.getByRole("button", { name: "info" }));
    expect(screen.getByText("Saved")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /dismiss notification/i }));
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("dismiss() with no id clears every toast", async () => {
    renderHarness();
    await userEvent.click(screen.getByRole("button", { name: "info" }));
    await userEvent.click(screen.getByRole("button", { name: "err" }));
    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(screen.getByText("Boom")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "clear" }));
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    expect(screen.queryByText("Boom")).not.toBeInTheDocument();
  });

  it("auto-dismisses after the default duration", () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );
    act(() => {
      screen.getByRole("button", { name: "info" }).click();
    });
    expect(screen.getByText("Saved")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("does not auto-dismiss when duration is 0", () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );
    act(() => {
      screen.getByTestId("sticky").click();
    });
    expect(screen.getByText("Sticky")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(60000);
    });
    expect(screen.getByText("Sticky")).toBeInTheDocument();
  });

  it("throws if useToast is used without a provider", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    function Orphan() {
      useToast();
      return null;
    }
    expect(() => render(<Orphan />)).toThrow(/useToast must be used within a ToastProvider/);
  });
});
