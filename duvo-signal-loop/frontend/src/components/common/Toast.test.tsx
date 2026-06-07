import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ToastProvider, useToast } from "./Toast";

function Harness() {
  const { toast, dismiss } = useToast();
  return (
    <div>
      <button onClick={() => toast({ title: "Run started", variant: "success" })}>
        success
      </button>
      <button onClick={() => toast({ title: "Boom", description: "It failed", variant: "error" })}>
        error
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

describe("Toast / useToast", () => {
  it("shows a toast with title and description when toast() is called", async () => {
    renderHarness();
    await userEvent.click(screen.getByRole("button", { name: "error" }));
    expect(screen.getByText("Boom")).toBeInTheDocument();
    expect(screen.getByText("It failed")).toBeInTheDocument();
  });

  it("renders an error toast as an assertive alert", async () => {
    renderHarness();
    await userEvent.click(screen.getByRole("button", { name: "error" }));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveAttribute("aria-live", "assertive");
  });

  it("renders a success toast as a polite status", async () => {
    renderHarness();
    await userEvent.click(screen.getByRole("button", { name: "success" }));
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent("Run started");
  });

  it("lets the user dismiss a toast via its close button", async () => {
    renderHarness();
    await userEvent.click(screen.getByRole("button", { name: "error" }));
    expect(screen.getByText("Boom")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByText("Boom")).not.toBeInTheDocument();
  });

  it("dismiss() clears all toasts", async () => {
    renderHarness();
    await userEvent.click(screen.getByRole("button", { name: "error" }));
    await userEvent.click(screen.getByRole("button", { name: "success" }));
    await userEvent.click(screen.getByRole("button", { name: "clear" }));
    expect(screen.queryByText("Boom")).not.toBeInTheDocument();
    expect(screen.queryByText("Run started")).not.toBeInTheDocument();
  });

  it("throws if useToast is used outside a ToastProvider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    function Orphan() {
      useToast();
      return null;
    }
    expect(() => render(<Orphan />)).toThrow(/ToastProvider/);
    spy.mockRestore();
  });

  describe("auto-dismiss", () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it("auto-dismisses a toast after its duration elapses", () => {
      function AutoHarness() {
        const { toast } = useToast();
        return (
          <button onClick={() => toast({ title: "Transient", duration: 1000 })}>go</button>
        );
      }
      render(
        <ToastProvider>
          <AutoHarness />
        </ToastProvider>,
      );
      act(() => {
        screen.getByRole("button", { name: "go" }).click();
      });
      expect(screen.getByText("Transient")).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(screen.queryByText("Transient")).not.toBeInTheDocument();
    });
  });
});
