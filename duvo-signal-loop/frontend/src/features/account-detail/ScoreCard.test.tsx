import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { ScoreCard } from "./ScoreCard";
import type { ICPScore } from "@/lib/api";

const score: ICPScore = {
  company_name: "Acme Corp",
  domain: "acme.com",
  score: 8,
  tier: "Tier 1",
  confidence: "high",
  why_fit: ["Mid-market manufacturer", "Active ERP migration"],
  why_not: ["No EU presence"],
  recommended_persona: "VP Finance",
  recommended_angle: "ERP cutover risk",
  reasoning: "Strong fit on size and signal.",
  needs_human_research: false,
  outreach: { persona: "VP Finance", subject: "s", first_line: "f", body: "b" },
};

describe("ScoreCard", () => {
  it("renders the score, tier and confidence", () => {
    render(<ScoreCard score={score} />);
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("Tier 1")).toBeInTheDocument();
    expect(screen.getByText(/high/i)).toBeInTheDocument();
  });

  it("lists every why_fit reason", () => {
    render(<ScoreCard score={score} />);
    expect(screen.getByText("Mid-market manufacturer")).toBeInTheDocument();
    expect(screen.getByText("Active ERP migration")).toBeInTheDocument();
  });

  it("lists every why_not reason", () => {
    render(<ScoreCard score={score} />);
    expect(screen.getByText("No EU presence")).toBeInTheDocument();
  });

  it("shows an empty hint when a reason list is empty", () => {
    render(<ScoreCard score={{ ...score, why_not: [] }} />);
    expect(screen.getByText(/none/i)).toBeInTheDocument();
  });

  it("renders a placeholder when no score is available yet", () => {
    render(<ScoreCard score={null} />);
    expect(screen.getByText(/no score/i)).toBeInTheDocument();
  });
});
