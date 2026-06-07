import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { OutreachDraftPreview } from "./OutreachDraftPreview";
import type { OutreachDraft } from "@/lib/api";

const draft: OutreachDraft = {
  persona: "VP Finance",
  subject: "ERP cutover risk at Acme",
  first_line: "Saw your S/4HANA migration is underway.",
  body: "Hi there,\n\nWe help finance teams de-risk ERP cutovers.\n\nBest,\nDuvo",
};

describe("OutreachDraftPreview", () => {
  it("renders the persona and subject", () => {
    render(<OutreachDraftPreview draft={draft} />);
    expect(screen.getByText(/VP Finance/)).toBeInTheDocument();
    expect(screen.getByText("ERP cutover risk at Acme")).toBeInTheDocument();
  });

  it("renders the first line and body", () => {
    render(<OutreachDraftPreview draft={draft} />);
    expect(screen.getByText("Saw your S/4HANA migration is underway.")).toBeInTheDocument();
    expect(screen.getByText(/de-risk ERP cutovers/)).toBeInTheDocument();
  });

  it("renders an empty state when no draft exists", () => {
    render(<OutreachDraftPreview draft={null} />);
    expect(screen.getByText(/no outreach draft/i)).toBeInTheDocument();
  });
});
