import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { DiffBadge } from "./DiffBadge";

describe("DiffBadge", () => {
  it("renders nothing when there is no prior run to diff against", () => {
    const { container } = render(
      <DiffBadge score={7} diff={{ changed: false, prev_score: null, prev_tier: null }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows an upward delta vs the last run", () => {
    render(
      <DiffBadge score={7} diff={{ changed: true, prev_score: 5, prev_tier: "Tier 2" }} />,
    );
    expect(screen.getByText(/▲\+2/)).toBeInTheDocument();
    expect(screen.getByText(/vs last run/)).toBeInTheDocument();
  });

  it("shows a downward delta vs the last run", () => {
    render(
      <DiffBadge score={4} diff={{ changed: true, prev_score: 6, prev_tier: "Tier 1" }} />,
    );
    expect(screen.getByText(/▼-2/)).toBeInTheDocument();
  });

  it("shows an unchanged marker when the score matches the prior run", () => {
    render(
      <DiffBadge score={6} diff={{ changed: true, prev_score: 6, prev_tier: "Tier 1" }} />,
    );
    expect(screen.getByText(/no change/i)).toBeInTheDocument();
  });

  it("renders nothing when the current score is unknown", () => {
    const { container } = render(
      <DiffBadge score={null} diff={{ changed: true, prev_score: 5, prev_tier: "Tier 2" }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
