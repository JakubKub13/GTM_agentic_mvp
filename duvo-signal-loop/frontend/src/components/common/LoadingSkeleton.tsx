import { cn } from "@/lib/utils";

export interface LoadingSkeletonProps {
  /** Number of shimmer placeholder lines to render (default 1). */
  lines?: number;
  /** Extra classes merged onto the status wrapper. */
  className?: string;
}

/**
 * Accessible loading placeholder. Renders shimmer lines inside an `aria-busy`
 * status region so screen readers announce that content is loading. Used while
 * a query/SSE snapshot is in flight (plan #22).
 */
export function LoadingSkeleton({ lines = 1, className }: LoadingSkeletonProps) {
  return (
    <div role="status" aria-busy="true" className={cn("space-y-2", className)}>
      {Array.from({ length: Math.max(1, lines) }).map((_, i) => (
        <div
          key={i}
          data-skeleton-line
          className="h-4 w-full animate-pulse rounded-md bg-muted"
        />
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}
