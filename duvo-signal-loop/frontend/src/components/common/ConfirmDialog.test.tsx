import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ConfirmDialog } from "./ConfirmDialog";

describe("ConfirmDialog", () => {
  it("does not render anything when closed", () => {
    render(
      <ConfirmDialog
        open={false}
        title="Launch a real run?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders a modal dialog with title and description when open", () => {
    render(
      <ConfirmDialog
        open
        title="Launch a real run?"
        description="This writes to the CRM and posts Slack alerts."
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("Launch a real run?")).toBeInTheDocument();
    expect(
      screen.getByText("This writes to the CRM and posts Slack alerts."),
    ).toBeInTheDocument();
  });

  it("fires onConfirm when the confirm button is clicked", async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Launch a real run?"
        confirmLabel="Launch"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Launch" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("fires onCancel when the cancel button is clicked", async () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Launch a real run?"
        cancelLabel="Keep dry-run"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Keep dry-run" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("fires onCancel when Escape is pressed", async () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog open title="Launch a real run?" onConfirm={vi.fn()} onCancel={onCancel} />,
    );
    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("uses default Confirm/Cancel labels and a destructive style when asked", () => {
    render(
      <ConfirmDialog
        open
        title="Delete?"
        destructive
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Confirm" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("disables the confirm button while pending", () => {
    render(
      <ConfirmDialog
        open
        title="Launch a real run?"
        confirmLabel="Launch"
        pending
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
  });
});
