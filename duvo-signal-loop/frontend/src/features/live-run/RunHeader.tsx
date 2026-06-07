import { cn } from "@/lib/utils";
import type { AccountRun, Run } from "@/lib/api";

import { StatusBadge } from "./StatusBadge";
import { ProgressBar } from "./ProgressBar";
import { ElapsedTimer } from "./ElapsedTimer";

export interface RunHeaderProps {
  /** The run row (null while the snapshot loads). */
  run: Run | null;
  /** Live account rows; used to compute done/total progress. */
  accounts: AccountRun[];
  /** Transport mode label (live / polling), surfaced as a small hint. */
  mode?: string;
  className?: string;
}

const TERMINAL_ACCOUNT = new Set(["done", "failed", "interrupted", "cancelled"]);

/**
 * Run header for the live view (plan #20): status badge (incl. interrupted/
 * cancelled), done/total ProgressBar, ElapsedTimer, triggered_by, and model/env
 * tags. Progress is computed from the live account rows so it tracks the SSE
 * stream even before the run's own counters are persisted.
 */
export function RunHeader({ run, accounts, mode, className }: RunHeaderProps) {
  const total = run?.accounts_total ?? accounts.length;
  const done = accounts.filter((a) => TERMINAL_ACCOUNT.has(a.status)).length;

  return (
    <header className={cn("flex flex-wrap items-center gap-x-6 gap-y-2", className)}>
      <div className="flex items-center gap-2">
        {run?.status ? <StatusBadge status={run.status} /> : <StatusBadge status="connecting" />}
        {run?.dry_run ? (
          <span className="rounded-md border border-input px-1.5 py-0.5 text-xs text-muted-foreground">
            dry-run
          </span>
        ) : null}
        {mode ? <span className="text-xs text-muted-foreground">· {mode}</span> : null}
      </div>

      <ProgressBar done={done} total={total} />

      <div className="flex items-center gap-1 text-sm text-muted-foreground">
        <span aria-hidden="true">⏱</span>
        <ElapsedTimer startedAt={run?.started_at ?? null} finishedAt={run?.finished_at ?? null} />
      </div>

      {run?.triggered_by ? (
        <span className="text-xs text-muted-foreground">by {run.triggered_by}</span>
      ) : null}

      <div className="flex items-center gap-1.5">
        {run?.model ? (
          <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            {run.model}
          </span>
        ) : null}
        {run?.app_env ? (
          <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            {run.app_env}
          </span>
        ) : null}
      </div>
    </header>
  );
}
