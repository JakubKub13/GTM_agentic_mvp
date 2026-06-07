import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { LoadingSkeleton } from "./LoadingSkeleton";

describe("LoadingSkeleton", () => {
  it("renders an aria-busy status region for screen readers", () => {
    render(<LoadingSkeleton />);
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(status).toHaveTextContent(/loading/i);
  });

  it("renders the requested number of placeholder lines", () => {
    const { container } = render(<LoadingSkeleton lines={4} />);
    expect(container.querySelectorAll("[data-skeleton-line]")).toHaveLength(4);
  });

  it("defaults to a single placeholder line", () => {
    const { container } = render(<LoadingSkeleton />);
    expect(container.querySelectorAll("[data-skeleton-line]")).toHaveLength(1);
  });

  it("merges a caller className onto the wrapper", () => {
    render(<LoadingSkeleton className="my-custom-class" />);
    expect(screen.getByRole("status")).toHaveClass("my-custom-class");
  });

  it("uses the animate-pulse shimmer on each line", () => {
    const { container } = render(<LoadingSkeleton lines={2} />);
    container.querySelectorAll("[data-skeleton-line]").forEach((line) => {
      expect(line).toHaveClass("animate-pulse");
    });
  });
});
