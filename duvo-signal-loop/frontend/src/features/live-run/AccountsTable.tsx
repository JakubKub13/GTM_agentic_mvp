import { cn } from "@/lib/utils";
import type { AccountRun } from "@/lib/api";
import { EmptyState } from "@/components/common";

import { AccountRow } from "./AccountRow";

export interface AccountsTableProps {
  /** Live account rows (seeded from snapshot, updated by SSE `account` events). */
  accounts: AccountRun[];
  /** Open the drill-down for a domain (plan #21). */
  onSelect?: (domain: string) => void;
  className?: string;
}

/** Sort order so running/pending float up and terminal rows settle below. */
const STATUS_RANK: Record<string, number> = {
  running: 0,
  pending: 1,
  done: 2,
  failed: 3,
  interrupted: 4,
  cancelled: 5,
};

function rank(status: string): number {
  return STATUS_RANK[status] ?? 9;
}

/**
 * The per-account live table (plan #20): one {@link AccountRow} per account,
 * ordered so in-flight accounts are visible first. Empty until the snapshot
 * pre-seeds the pending rows.
 */
export function AccountsTable({ accounts, onSelect, className }: AccountsTableProps) {
  if (accounts.length === 0) {
    return <EmptyState title="No accounts yet" description="Waiting for the run snapshot…" />;
  }

  const sorted = [...accounts].sort(
    (a, b) => rank(a.status) - rank(b.status) || a.domain.localeCompare(b.domain),
  );

  return (
    <div className={cn("overflow-x-auto rounded-xl border", className)}>
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th className="px-3 py-2 font-medium">Account</th>
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Score</th>
            <th className="px-3 py-2 font-medium">Tier</th>
            <th className="px-3 py-2 font-medium">Confidence</th>
            <th className="px-3 py-2 text-right font-medium">Signals</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((account) => (
            <AccountRow key={account.domain} account={account} onSelect={onSelect} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
