import { cn } from "@/lib/utils";

export interface ScoreBadgeProps {
  /** The ICP score 1–10, or null/undefined while the account has not finished. */
  score: number | null | undefined;
  className?: string;
}

/** Colour band for an ICP score: high (>=7) green, mid (4–6) amber, low (<4) muted. */
function band(score: number): string {
  if (score >= 7) return "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-100";
  if (score >= 4) return "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100";
  return "bg-muted text-muted-foreground";
}

/**
 * Compact ICP-score chip (plan #20). Renders an em-dash placeholder until a
 * numeric score lands (account still pending/running).
 */
export function ScoreBadge({ score, className }: ScoreBadgeProps) {
  if (score == null) {
    return <span className={cn("text-muted-foreground", className)}>—</span>;
  }
  return (
    <span
      className={cn(
        "inline-flex h-6 min-w-6 items-center justify-center rounded-md px-1.5 text-sm font-semibold tabular-nums",
        band(score),
        className,
      )}
    >
      {score}
    </span>
  );
}
