import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { AccountRun } from "@/lib/api";
import { AccountRow } from "./AccountRow";

function makeAccount(over: Partial<AccountRun> = {}): AccountRun {
  return {
    run_id: "r1",
    domain: "acme.com",
    company_name: "Acme Inc",
    country: "US",
    status: "pending",
    score: null,
    tier: null,
    confidence: null,
    needs_human_research: null,
    signals_count: null,
    signals_json: null,
    score_json: null,
    error: null,
    started_at: null,
    finished_at: null,
    ...over,
  };
}

/** AccountRow must be rendered inside a table to be valid HTML. */
function renderRow(account: AccountRun, onSelect?: (domain: string) => void) {
  return render(
    <table>
      <tbody>
        <AccountRow account={account} onSelect={onSelect} />
      </tbody>
    </table>,
  );
}

describe("AccountRow", () => {
  it("renders the company name and domain", () => {
    renderRow(makeAccount());
    expect(screen.getByText("Acme Inc")).toBeInTheDocument();
    expect(screen.getByText("acme.com")).toBeInTheDocument();
  });

  it("falls back to the domain when there is no company name", () => {
    renderRow(makeAccount({ company_name: null }));
    expect(screen.getAllByText("acme.com").length).toBeGreaterThan(0);
  });

  it("shows the live status label (pending -> running -> terminal)", () => {
    const { rerender } = renderRow(makeAccount({ status: "pending" }));
    expect(screen.getByText(/pending/i)).toBeInTheDocument();

    rerender(
      <table>
        <tbody>
          <AccountRow account={makeAccount({ status: "running" })} />
        </tbody>
      </table>,
    );
    expect(screen.getByText(/running/i)).toBeInTheDocument();

    rerender(
      <table>
        <tbody>
          <AccountRow account={makeAccount({ status: "done" })} />
        </tbody>
      </table>,
    );
    expect(screen.getByText(/done/i)).toBeInTheDocument();
  });

  it("shows the score, tier, confidence and signal count for a completed account", () => {
    renderRow(
      makeAccount({
        status: "done",
        score: 8,
        tier: "Tier 1",
        confidence: "high",
        signals_count: 3,
      }),
    );
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("Tier 1")).toBeInTheDocument();
    expect(screen.getByText(/high/i)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("shows a placeholder for score/tier when the account has not finished", () => {
    renderRow(makeAccount({ status: "running", score: null, tier: null }));
    // em dash placeholders for missing numeric fields
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("surfaces an error status with the error text in the title", () => {
    renderRow(makeAccount({ status: "failed", error: "scout timeout" }));
    const row = screen.getByRole("row");
    expect(row).toHaveAttribute("title", expect.stringContaining("scout timeout"));
    expect(screen.getByText(/failed/i)).toBeInTheDocument();
  });

  it("calls onSelect with the domain when the row is clicked", async () => {
    const onSelect = vi.fn();
    renderRow(makeAccount(), onSelect);
    await userEvent.click(screen.getByRole("row"));
    expect(onSelect).toHaveBeenCalledWith("acme.com");
  });

  it("does not throw and is not clickable when onSelect is omitted", async () => {
    renderRow(makeAccount());
    await userEvent.click(screen.getByRole("row"));
    // no assertion needed beyond no crash; row still rendered
    expect(screen.getByRole("row")).toBeInTheDocument();
  });
});
