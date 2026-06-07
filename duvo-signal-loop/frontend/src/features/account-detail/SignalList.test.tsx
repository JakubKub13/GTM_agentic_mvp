import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { SignalList } from "./SignalList";
import type { Signal } from "@/lib/api";

const signals: Signal[] = [
  {
    signal_type: "erp_migration",
    title: "Acme migrates to S/4HANA",
    summary: "Public case study about the cutover.",
    source_url: "https://example.com/acme-erp",
    published_date: "2026-01-15",
    relevance: "Direct ERP migration signal.",
  },
  {
    signal_type: "hiring",
    title: "Hiring an SAP lead",
    summary: "Job posting.",
    source_url: "https://jobs.example.com/sap-lead",
    published_date: null,
    relevance: "Hiring momentum.",
  },
];

describe("SignalList", () => {
  it("renders each signal title", () => {
    render(<SignalList signals={signals} />);
    expect(screen.getByText("Acme migrates to S/4HANA")).toBeInTheDocument();
    expect(screen.getByText("Hiring an SAP lead")).toBeInTheDocument();
  });

  it("links each signal to its source url in a new tab", () => {
    render(<SignalList signals={signals} />);
    const link = screen.getByRole("link", { name: /Acme migrates to S\/4HANA/ });
    expect(link).toHaveAttribute("href", "https://example.com/acme-erp");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("shows the signal type label", () => {
    render(<SignalList signals={signals} />);
    expect(screen.getByText("ERP migration")).toBeInTheDocument();
    expect(screen.getByText("Hiring")).toBeInTheDocument();
  });

  it("shows the published date when present", () => {
    render(<SignalList signals={signals} />);
    expect(screen.getByText("2026-01-15")).toBeInTheDocument();
  });

  it("renders an empty state when there are no signals", () => {
    render(<SignalList signals={[]} />);
    expect(screen.getByText(/no signals/i)).toBeInTheDocument();
  });
});
