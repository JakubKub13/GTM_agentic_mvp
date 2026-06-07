import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import type { ToolEvent } from "@/lib/api";

export interface ToolCallFeedProps {
  /** Tool events streamed from SSE (plan #9 redacted payloads), oldest first. */
  events: ToolEvent[];
  className?: string;
}

/** "scout:hiring" | "router" — agent role with its beat when present. */
function agentBeat(event: ToolEvent): string {
  if (!event.agent) return event.beat ?? "";
  return event.beat ? `${event.agent}:${event.beat}` : event.agent;
}

/** Lowercased haystack a filter substring is matched against. */
function haystack(event: ToolEvent): string {
  return [event.account, event.agent, event.beat, event.tool, event.arg_summary]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

/**
 * Auto-scrolling, filterable feed of `account · agent:beat · tool(args)` lines
 * built from SSE `tool` events (plan #20). The feed is a `log` live region; new
 * lines scroll into view as they arrive.
 */
export function ToolCallFeed({ events, className }: ToolCallFeedProps) {
  const [filter, setFilter] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return events;
    return events.filter((e) => haystack(e).includes(needle));
  }, [events, filter]);

  useEffect(() => {
    // scrollIntoView is unimplemented in jsdom; guard so tests + SSR don't throw.
    bottomRef.current?.scrollIntoView?.({ block: "end" });
  }, [shown.length]);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <input
        type="text"
        aria-label="Filter tool calls"
        placeholder="Filter by account, agent or tool…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        className="h-8 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      />
      <div
        role="log"
        aria-live="polite"
        aria-label="Agent tool calls"
        className="h-72 overflow-y-auto rounded-md border bg-card p-2 font-mono text-xs"
      >
        {events.length === 0 ? (
          <p className="p-2 text-muted-foreground">No tool calls yet.</p>
        ) : shown.length === 0 ? (
          <p className="p-2 text-muted-foreground">No tool calls match the filter.</p>
        ) : (
          <ul className="space-y-0.5">
            {shown.map((e, i) => (
              <li key={`${e.ts}-${i}`} className="flex flex-wrap items-baseline gap-x-2 leading-5">
                <span className="text-muted-foreground">{e.account}</span>
                <span className="text-blue-600 dark:text-blue-400">{agentBeat(e)}</span>
                <span className="text-foreground">
                  {e.tool}
                  {e.arg_summary ? `(${e.arg_summary})` : "()"}
                </span>
              </li>
            ))}
          </ul>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
