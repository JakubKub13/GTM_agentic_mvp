import { useEffect, useState } from "react";

export interface ElapsedTimerProps {
  /** ISO start time; null renders a dash. */
  startedAt: string | null;
  /** ISO finish time; when set the timer freezes at the final duration. */
  finishedAt?: string | null;
}

/** Format a millisecond duration as H:MM:SS or M:SS. */
function format(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(h > 0 ? 2 : 1, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/**
 * Live elapsed-time readout (plan #20). Ticks once a second while the run is
 * active; once `finishedAt` is set it freezes at the final duration. Renders a
 * dash until a `startedAt` is known.
 */
export function ElapsedTimer({ startedAt, finishedAt }: ElapsedTimerProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!startedAt || finishedAt) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [startedAt, finishedAt]);

  if (!startedAt) return <span className="tabular-nums">—</span>;

  const start = new Date(startedAt).getTime();
  const end = finishedAt ? new Date(finishedAt).getTime() : now;
  return (
    <span className="tabular-nums" aria-label="Elapsed time">
      {format(end - start)}
    </span>
  );
}
