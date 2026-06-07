import { cn } from "@/lib/utils";

export interface ProgressBarProps {
  /** Accounts in a terminal state. */
  done: number;
  /** Total accounts in the run. */
  total: number;
  className?: string;
}

/**
 * Done/total progress for a run (plan #20). Renders an accessible progressbar
 * with a numeric `done / total` label; a zero total reads as 0%.
 */
export function ProgressBar({ done, total, className }: ProgressBarProps) {
  const safeTotal = Math.max(0, total);
  const safeDone = Math.min(Math.max(0, done), safeTotal || done);
  const pct = safeTotal > 0 ? Math.round((safeDone / safeTotal) * 100) : 0;

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={safeTotal}
        aria-valuenow={safeDone}
        className="h-2 w-40 overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full rounded-full bg-primary transition-[width]"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-muted-foreground">
        {safeDone} / {safeTotal}
      </span>
    </div>
  );
}
