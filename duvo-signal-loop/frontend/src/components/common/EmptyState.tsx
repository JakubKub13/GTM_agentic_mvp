import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export interface EmptyStateProps {
  /** Headline shown to the user (e.g. "No runs yet"). */
  title: string;
  /** Optional supporting copy under the title. */
  description?: string;
  /** Optional leading icon/illustration. */
  icon?: ReactNode;
  /** Label for the call-to-action button; rendered only with `onAction`. */
  actionLabel?: string;
  /** Click handler for the call-to-action button; rendered only with `actionLabel`. */
  onAction?: () => void;
  className?: string;
}

/**
 * Centered placeholder for a list/view that has no data yet, with an optional
 * call-to-action (plan #22). The action button renders only when both
 * `actionLabel` and `onAction` are supplied.
 */
export function EmptyState({
  title,
  description,
  icon,
  actionLabel,
  onAction,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-10 text-center",
        className,
      )}
    >
      {icon ? <div className="text-muted-foreground">{icon}</div> : null}
      <h3 className="text-base font-semibold leading-none tracking-tight">{title}</h3>
      {description ? (
        <p data-testid="empty-state-description" className="max-w-sm text-sm text-muted-foreground">
          {description}
        </p>
      ) : null}
      {actionLabel && onAction ? (
        <Button onClick={onAction} className="mt-1">
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}
