import { cn } from "@/lib/utils";

export interface StatusBadgeProps {
  /** Account or run status (pending/running/done/failed/interrupted/cancelled). */
  status: string;
  className?: string;
}

/** Colour band per lifecycle status (plan #20: incl. interrupted/cancelled). */
function statusClass(status: string): string {
  switch (status) {
    case "done":
      return "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-100";
    case "running":
      return "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-100";
    case "pending":
      return "bg-muted text-muted-foreground";
    case "failed":
      return "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-100";
    case "interrupted":
      return "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100";
    case "cancelled":
      return "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200";
    default:
      return "bg-muted text-muted-foreground";
  }
}

/**
 * Lifecycle status chip used by both RunHeader and AccountRow. A `running`
 * status pulses so the live view reads as active (plan #20).
 */
export function StatusBadge({ status, className }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium capitalize",
        statusClass(status),
        className,
      )}
    >
      {status === "running" ? (
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 animate-pulse rounded-full bg-current"
        />
      ) : null}
      {status}
    </span>
  );
}
