import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ToastProvider } from "@/components/common";

import { NewRunForm } from "./NewRunForm";

// useStartRun is exercised on its own; here we only assert NewRunForm calls it
// with the right body and gates real runs behind the confirm dialog.
const mutate = vi.fn();
let isPending = false;
vi.mock("./useStartRun", () => ({
  useStartRun: () => ({ mutate, isPending }),
}));

function renderForm() {
  return render(
    <ToastProvider>
      <NewRunForm />
    </ToastProvider>,
  );
}

describe("NewRunForm", () => {
  beforeEach(() => {
    mutate.mockReset();
    isPending = false;
  });

  it("defaults the dry-run toggle ON", () => {
    renderForm();
    const dryRun = screen.getByRole("switch", { name: /dry.?run/i });
    expect(dryRun).toBeChecked();
  });

  it("submits a dry run directly without a confirm dialog", async () => {
    renderForm();
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toMatchObject({ dry_run: true });
  });

  it("gates a real (non-dry) run behind the confirm dialog and only fires on confirm", async () => {
    renderForm();

    // Turn dry-run OFF -> this becomes a real, side-effecting run.
    await userEvent.click(screen.getByRole("switch", { name: /dry.?run/i }));
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));

    // No POST yet — the confirm dialog must gate it.
    expect(mutate).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();

    // Cancel -> still no POST.
    await userEvent.click(screen.getByRole("button", { name: /cancel|keep dry/i }));
    expect(mutate).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // Re-open and confirm -> POST fires with dry_run:false.
    await userEvent.click(screen.getByRole("button", { name: /start run/i }));
    await screen.findByRole("dialog");
    await userEvent.click(screen.getByRole("button", { name: /launch|confirm/i }));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toMatchObject({ dry_run: false });
  });

  it("includes concurrency, skip-done-today and test-email in the request body", async () => {
    renderForm();

    await userEvent.clear(screen.getByLabelText(/concurrency/i));
    await userEvent.type(screen.getByLabelText(/concurrency/i), "4");
    await userEvent.click(screen.getByRole("switch", { name: /skip.*done.*today/i }));
    await userEvent.type(screen.getByLabelText(/test email/i), "qa@duvo.dev");

    await userEvent.click(screen.getByRole("button", { name: /start run/i }));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toMatchObject({
      dry_run: true,
      concurrency: 4,
      skip_done_today: true,
      test_email: "qa@duvo.dev",
    });
  });

  it("disables the submit button while a run is being created", () => {
    isPending = true;
    renderForm();
    expect(screen.getByRole("button", { name: /start run|starting/i })).toBeDisabled();
  });
});
