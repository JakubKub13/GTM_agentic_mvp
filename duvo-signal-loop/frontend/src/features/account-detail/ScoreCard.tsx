import type { ICPScore } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/common";

export interface ScoreCardProps {
  /** The full ICP score (parsed from account_runs.score_json), or null pre-terminal. */
  score: ICPScore | null;
}

function ReasonList({ title, reasons }: { title: string; reasons: string[] }) {
  return (
    <div className="space-y-1">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h4>
      {reasons.length ? (
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {reasons.map((reason, i) => (
            <li key={i}>{reason}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm italic text-muted-foreground">None</p>
      )}
    </div>
  );
}

/**
 * Why-fit / why-not score card for the drill-down drawer (plan #21).
 *
 * Shows the headline score/tier/confidence and the LLM's structured rationale.
 * Falls back to an empty state when the account has no terminal score yet.
 */
export function ScoreCard({ score }: ScoreCardProps) {
  if (!score) {
    return <EmptyState title="No score yet" description="This account has not finished scoring." />;
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle>Score</CardTitle>
        <div className="flex items-center gap-2">
          <span className="text-2xl font-bold tabular-nums">{score.score}</span>
          <span className="rounded-md border px-2 py-0.5 text-xs font-medium">{score.tier}</span>
          <span className="text-xs capitalize text-muted-foreground">{score.confidence}</span>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2">
        <ReasonList title="Why it fits" reasons={score.why_fit} />
        <ReasonList title="Why it might not" reasons={score.why_not} />
      </CardContent>
    </Card>
  );
}
