import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { AccountDetailDrawer } from "./AccountDetailDrawer";
import { api, type AccountDetail, type ICPScore, type Signal } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, getAccount: vi.fn() } };
});

const icp: ICPScore = {
  company_name: "Acme",
  domain: "acme.com",
  score: 8,
  tier: "Tier 1",
  confidence: "high",
  why_fit: ["Mid-market manufacturer"],
  why_not: ["No EU presence"],
  recommended_persona: "VP Finance",
  recommended_angle: "ERP cutover risk",
  reasoning: "Strong fit.",
  needs_human_research: false,
  outreach: {
    persona: "VP Finance",
    subject: "ERP cutover risk at Acme",
    first_line: "Saw your S/4HANA migration is underway.",
    body: "Hi there,",
  },
};

const signals: Signal[] = [
  {
    signal_type: "erp_migration",
    title: "Acme migrates to S/4HANA",
    summary: "Public case study.",
    source_url: "https://example.com/acme-erp",
    published_date: "2026-01-15",
    relevance: "Direct.",
  },
];

const detail: AccountDetail = {
  account: {
    run_id: "r1",
    domain: "acme.com",
    company_name: "Acme",
    country: null,
    status: "done",
    score: 8,
    tier: "Tier 1",
    confidence: "high",
    needs_human_research: 0,
    signals_count: 1,
    signals_json: JSON.stringify(signals),
    score_json: JSON.stringify(icp),
    error: null,
    started_at: null,
    finished_at: null,
    agent_log_json: JSON.stringify(["scout:hiring exa_search(query=...)"]),
  },
  diff: { changed: true, prev_score: 6, prev_tier: "Tier 1" },
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("AccountDetailDrawer", () => {
  beforeEach(() => {
    vi.mocked(api.getAccount).mockReset();
  });

  it("renders nothing when closed", () => {
    vi.mocked(api.getAccount).mockResolvedValue(detail);
    render(
      <AccountDetailDrawer runId="r1" domain={null} open={false} onClose={vi.fn()} />,
      { wrapper },
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.getAccount).not.toHaveBeenCalled();
  });

  it("fetches and renders the score, signals, outreach, diff and agent log", async () => {
    vi.mocked(api.getAccount).mockResolvedValue(detail);
    render(
      <AccountDetailDrawer runId="r1" domain="acme.com" open onClose={vi.fn()} />,
      { wrapper },
    );

    await waitFor(() => expect(api.getAccount).toHaveBeenCalledWith("r1", "acme.com"));

    // ScoreCard
    expect(await screen.findByText("Mid-market manufacturer")).toBeInTheDocument();
    expect(screen.getByText("No EU presence")).toBeInTheDocument();
    // SignalList
    expect(screen.getByRole("link", { name: /Acme migrates to S\/4HANA/ })).toBeInTheDocument();
    // OutreachDraftPreview
    expect(screen.getByText("ERP cutover risk at Acme")).toBeInTheDocument();
    // DiffBadge (8 vs 6 = +2)
    expect(screen.getByText(/▲\+2/)).toBeInTheDocument();
    // AgentLogViewer
    expect(screen.getByText("scout:hiring exa_search(query=...)")).toBeInTheDocument();
  });

  it("shows a loading skeleton while the detail is in flight", () => {
    vi.mocked(api.getAccount).mockReturnValue(new Promise(() => {}));
    render(
      <AccountDetailDrawer runId="r1" domain="acme.com" open onClose={vi.fn()} />,
      { wrapper },
    );
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows an error state when the fetch fails", async () => {
    vi.mocked(api.getAccount).mockRejectedValue(new Error("boom"));
    render(
      <AccountDetailDrawer runId="r1" domain="acme.com" open onClose={vi.fn()} />,
      { wrapper },
    );
    expect(await screen.findByText(/couldn't load|could not load|failed/i)).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", async () => {
    vi.mocked(api.getAccount).mockResolvedValue(detail);
    const onClose = vi.fn();
    render(
      <AccountDetailDrawer runId="r1" domain="acme.com" open onClose={onClose} />,
      { wrapper },
    );
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Escape is pressed", async () => {
    vi.mocked(api.getAccount).mockResolvedValue(detail);
    const onClose = vi.fn();
    render(
      <AccountDetailDrawer runId="r1" domain="acme.com" open onClose={onClose} />,
      { wrapper },
    );
    await screen.findByText("Mid-market manufacturer");
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("tolerates malformed score_json/signals_json without crashing", async () => {
    vi.mocked(api.getAccount).mockResolvedValue({
      ...detail,
      account: { ...detail.account, score_json: "{bad", signals_json: "nope" },
    });
    render(
      <AccountDetailDrawer runId="r1" domain="acme.com" open onClose={vi.fn()} />,
      { wrapper },
    );
    // Falls back to empty states instead of throwing.
    expect(await screen.findByText(/no score yet/i)).toBeInTheDocument();
    expect(screen.getByText(/no signals/i)).toBeInTheDocument();
  });
});
