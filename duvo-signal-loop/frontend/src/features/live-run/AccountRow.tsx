import { cn } from "@/lib/utils";
import type { AccountRun } from "@/lib/api";

import { ScoreBadge } from "./ScoreBadge";
import { TierBadge } from "./TierBadge";
import { StatusBadge } from "./StatusBadge";

export interface AccountRowProps {
  /** The live `account_runs` row (merged with SSE updates by the view). */
  account: AccountRun;
  /** Open the drill-down for this account's domain (plan #21). Omit for read-only. */
  onSelect?: (domain: string) => void;
}

/**
 * One row of the {@link AccountsTable}: company/domain, live status, ICP score,
 * tier, confidence and signal count (plan #20). Clicking the row opens the
 * account drill-down when `onSelect` is supplied.
 */
export function AccountRow({ account, onSelect }: AccountRowProps) {
  const clickable = Boolean(onSelect);
  const title = account.error
    ? `${account.domain} — ${account.error}`
    : account.domain;

  return (
    <tr
      title={title}
      onClick={onSelect ? () => onSelect(account.domain) : undefined}
      className={cn(
        "border-b text-sm last:border-0",
        clickable && "cursor-pointer hover:bg-muted/50",
      )}
    >
      <td className="px-3 py-2">
        <div className="font-medium">{account.company_name ?? account.domain}</div>
        <div className="text-xs text-muted-foreground">{account.domain}</div>
      </td>
      <td className="px-3 py-2">
        <StatusBadge status={account.status} />
      </td>
      <td className="px-3 py-2">
        <ScoreBadge score={account.score} />
      </td>
      <td className="px-3 py-2">
        <TierBadge tier={account.tier} />
      </td>
      <td className="px-3 py-2 capitalize text-muted-foreground">
        {account.confidence ?? "—"}
      </td>
      <td className="px-3 py-2 tabular-nums text-right">
        {account.signals_count ?? "—"}
      </td>
    </tr>
  );
}
