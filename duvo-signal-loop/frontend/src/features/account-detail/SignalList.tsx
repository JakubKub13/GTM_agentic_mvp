import type { Signal } from "@/lib/api";
import { EmptyState } from "@/components/common";

export interface SignalListProps {
  /** Signals surfaced by the scouts (parsed from account_runs.signals_json). */
  signals: Signal[];
}

const TYPE_LABEL: Record<Signal["signal_type"], string> = {
  erp_migration: "ERP migration",
  hiring: "Hiring",
  ma_leadership: "M&A / leadership",
  pain: "Pain",
};

/**
 * The account's signals with source links, dates and type (plan #21).
 *
 * Each title links out to the source in a new tab (`noopener`). The published
 * date renders only when known; empty input yields an empty state.
 */
export function SignalList({ signals }: SignalListProps) {
  if (!signals.length) {
    return <EmptyState title="No signals" description="No buying signals were found for this account." />;
  }

  return (
    <ul className="space-y-3">
      {signals.map((signal, i) => (
        <li key={`${signal.source_url}-${i}`} className="rounded-lg border p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
              {TYPE_LABEL[signal.signal_type] ?? signal.signal_type}
            </span>
            {signal.published_date ? (
              <time className="text-xs text-muted-foreground">{signal.published_date}</time>
            ) : null}
          </div>
          <a
            href={signal.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 block text-sm font-medium text-primary underline-offset-4 hover:underline"
          >
            {signal.title}
          </a>
          <p className="mt-1 text-sm text-muted-foreground">{signal.summary}</p>
        </li>
      ))}
    </ul>
  );
}
