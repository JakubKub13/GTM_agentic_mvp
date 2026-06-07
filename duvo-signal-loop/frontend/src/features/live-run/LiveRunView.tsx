import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LoadingSkeleton } from "@/components/common";

import { useRunStream, type UseRunStreamOptions } from "./useRunStream";
import { RunHeader } from "./RunHeader";
import { AccountsTable } from "./AccountsTable";
import { ToolCallFeed } from "./ToolCallFeed";

export interface LiveRunViewProps {
  /** Run to watch live (plan #20 centerpiece). */
  runId: string;
  /** Open the account drill-down (plan #21). */
  onSelectAccount?: (domain: string) => void;
  /** Stream tuning, forwarded to {@link useRunStream}. */
  streamOptions?: UseRunStreamOptions;
}

/**
 * The Live Run view (plan #20): wires {@link useRunStream} to {@link RunHeader},
 * {@link AccountsTable} and the {@link ToolCallFeed}. The hook owns snapshot
 * replay, SSE reconnect and the polling fallback; this component is the layout.
 */
export function LiveRunView({ runId, onSelectAccount, streamOptions }: LiveRunViewProps) {
  const { run, accounts, toolEvents, mode, loading, error } = useRunStream(
    runId,
    streamOptions,
  );

  if (error) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-destructive">
          Failed to load run {runId}: {error}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-4">
          {loading ? (
            <LoadingSkeleton lines={2} />
          ) : (
            <RunHeader run={run} accounts={accounts} mode={mode} />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Accounts</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? <LoadingSkeleton lines={4} /> : (
              <AccountsTable accounts={accounts} onSelect={onSelectAccount} />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Tool calls</CardTitle>
          </CardHeader>
          <CardContent>
            <ToolCallFeed events={toolEvents} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
