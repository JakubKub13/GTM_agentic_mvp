import { cn } from "@/lib/utils";

export interface TierBadgeProps {
  /** "Tier 1" | "Tier 2" | "Tier 3" (or any backend string), null until scored. */
  tier: string | null | undefined;
  className?: string;
}

/** Tier 1 is the strongest fit, so it gets the most prominent styling. */
function tierClass(tier: string): string {
  if (tier === "Tier 1")
    return "border-transparent bg-primary text-primary-foreground";
  if (tier === "Tier 2") return "border-input bg-background text-foreground";
  return "border-transparent bg-muted text-muted-foreground";
}

/** Compact tier chip (plan #20); em-dash placeholder until scored. */
export function TierBadge({ tier, className }: TierBadgeProps) {
  if (tier == null || tier === "") {
    return <span className={cn("text-muted-foreground", className)}>—</span>;
  }
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
        tierClass(tier),
        className,
      )}
    >
      {tier}
    </span>
  );
}
