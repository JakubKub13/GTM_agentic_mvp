import { EmptyState } from "@/components/common";

export interface AgentLogViewerProps {
  /**
   * The persisted per-account agent log (account_runs.agent_log_json, plan #10):
   * a JSON-encoded `list[str]` of tool-call lines. Null until the account completes.
   */
  agentLogJson: string | null | undefined;
}

/** Parse the persisted log defensively — malformed/empty JSON yields []. */
function parseLog(agentLogJson: string | null | undefined): string[] {
  if (!agentLogJson) return [];
  try {
    const parsed: unknown = JSON.parse(agentLogJson);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((line): line is string => typeof line === "string");
  } catch {
    return [];
  }
}

/**
 * Read-only viewer for an account's persisted tool-call trail (plan #21).
 *
 * This is the durable audit trail captured at account completion — distinct
 * from the ephemeral live `ToolCallFeed` (plan #10). Empty/missing/malformed
 * logs degrade to an empty state.
 */
export function AgentLogViewer({ agentLogJson }: AgentLogViewerProps) {
  const lines = parseLog(agentLogJson);

  if (!lines.length) {
    return (
      <EmptyState
        title="No agent log"
        description="No tool calls were recorded for this account yet."
      />
    );
  }

  return (
    <ol className="space-y-1 rounded-lg border bg-muted/30 p-3 font-mono text-xs">
      {lines.map((line, i) => (
        <li key={i} className="flex gap-2">
          <span className="select-none text-muted-foreground tabular-nums">
            {String(i + 1).padStart(2, "0")}
          </span>
          <code className="whitespace-pre-wrap break-all">{line}</code>
        </li>
      ))}
    </ol>
  );
}
