import type { AccountDiff } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface DiffBadgeProps {
  /** The current run's score (null while unknown / pre-terminal). */
  score: number | null;
  /** Score/tier diff against the most recent real prior run (queries.get_account_detail). */
  diff: AccountDiff;
  className?: string;
}

/**
 * Score delta vs the last real run, e.g. "▲+2 vs last run" (plan #21).
 *
 * Renders nothing when there is no comparable prior run (`changed` false or
 * `prev_score` null) or when the current score is unknown — there is nothing
 * honest to compare.
 */
export function DiffBadge({ score, diff, className }: DiffBadgeProps) {
  if (score == null || !diff.changed || diff.prev_score == null) return null;

  const delta = score - diff.prev_score;
  const up = delta > 0;
  const down = delta < 0;

  const label = up
    ? `▲+${delta}`
    : down
      ? `▼${delta}` // delta already negative, e.g. "▼-2"
      : "no change";

  return (
    <span
      data-testid="diff-badge"
      title={`Previous: ${diff.prev_score}${diff.prev_tier ? ` (${diff.prev_tier})` : ""}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
        up && "border-emerald-300 bg-emerald-50 text-emerald-700",
        down && "border-rose-300 bg-rose-50 text-rose-700",
        !up && !down && "border-muted bg-muted text-muted-foreground",
        className,
      )}
    >
      <span>{label}</span>
      <span className="text-muted-foreground">vs last run</span>
    </span>
  );
}
