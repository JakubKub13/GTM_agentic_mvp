import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("renders the title", () => {
    render(<EmptyState title="No runs yet" />);
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
  });

  it("renders an optional description", () => {
    render(<EmptyState title="No runs yet" description="Start a run to see it here." />);
    expect(screen.getByText("Start a run to see it here.")).toBeInTheDocument();
  });

  it("omits the description node when none is given", () => {
    render(<EmptyState title="No runs yet" />);
    expect(screen.queryByTestId("empty-state-description")).not.toBeInTheDocument();
  });

  it("renders an action button and fires its handler on click", async () => {
    const onAction = vi.fn();
    render(
      <EmptyState title="No runs yet" actionLabel="New run" onAction={onAction} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "New run" }));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("does not render an action button without both label and handler", () => {
    render(<EmptyState title="No runs yet" actionLabel="New run" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
