import { useEffect, useReducer, useRef } from "react";

import {
  api,
  openRunStream,
  type AccountEvent,
  type AccountRun,
  type Run,
  type RunStreamEvent,
  type StatusEvent,
  type ToolEvent,
} from "@/lib/api";

/** Connection mode the view can surface (plan #20: SSE with polling fallback). */
export type StreamMode = "connecting" | "live" | "polling" | "closed";

export interface RunStreamState {
  /** The run row from the snapshot, updated by `status` events. Null until loaded. */
  run: Run | null;
  /** Live account rows keyed by domain, seeded from the snapshot. */
  accounts: AccountRun[];
  /** Live, ephemeral tool-call feed (plan #10: not replayed for in-flight accounts). */
  toolEvents: ToolEvent[];
  /** Current transport mode. */
  mode: StreamMode;
  /** True until the first snapshot resolves. */
  loading: boolean;
  /** Snapshot load error, if any. */
  error: string | null;
}

type Action =
  | { kind: "snapshot"; run: Run; accounts: AccountRun[] }
  | { kind: "snapshot-error"; message: string }
  | { kind: "account"; event: AccountEvent }
  | { kind: "status"; event: StatusEvent }
  | { kind: "tool"; event: ToolEvent }
  | { kind: "mode"; mode: StreamMode };

const MAX_TOOL_EVENTS = 1000;

const initialState: RunStreamState = {
  run: null,
  accounts: [],
  toolEvents: [],
  mode: "connecting",
  loading: true,
  error: null,
};

/** Merge an `account` SSE event onto the matching snapshot row (by domain). */
function applyAccountEvent(accounts: AccountRun[], event: AccountEvent): AccountRun[] {
  let found = false;
  const next = accounts.map((a) => {
    if (a.domain !== event.account) return a;
    found = true;
    return {
      ...a,
      status: event.status,
      score: event.score ?? a.score,
      tier: event.tier ?? a.tier,
      confidence: event.confidence ?? a.confidence,
      signals_count: event.signals_count ?? a.signals_count,
    };
  });
  if (found) return next;
  // An account we never saw in the snapshot — append a minimal row.
  return [
    ...next,
    {
      run_id: event.run_id,
      domain: event.account,
      company_name: null,
      country: null,
      status: event.status,
      score: event.score ?? null,
      tier: event.tier ?? null,
      confidence: event.confidence ?? null,
      needs_human_research: null,
      signals_count: event.signals_count ?? null,
      signals_json: null,
      score_json: null,
      error: null,
      started_at: null,
      finished_at: null,
    },
  ];
}

function reducer(state: RunStreamState, action: Action): RunStreamState {
  switch (action.kind) {
    case "snapshot":
      return { ...state, run: action.run, accounts: action.accounts, loading: false, error: null };
    case "snapshot-error":
      return { ...state, loading: false, error: action.message };
    case "account":
      return { ...state, accounts: applyAccountEvent(state.accounts, action.event) };
    case "status":
      return {
        ...state,
        run: state.run ? { ...state.run, status: action.event.status } : state.run,
      };
    case "tool": {
      const events = [...state.toolEvents, action.event];
      // Bound the live feed so a long run can't grow memory unbounded.
      return {
        ...state,
        toolEvents: events.length > MAX_TOOL_EVENTS ? events.slice(-MAX_TOOL_EVENTS) : events,
      };
    }
    case "mode":
      return { ...state, mode: action.mode };
  }
}

/** A run status that means the pipeline has stopped — no need to keep streaming. */
function isTerminal(status: string | null | undefined): boolean {
  return (
    status === "done" ||
    status === "failed" ||
    status === "interrupted" ||
    status === "cancelled"
  );
}

export interface UseRunStreamOptions {
  /** Delay before re-opening the SSE after a drop (ms). */
  reconnectDelayMs?: number;
  /** Consecutive SSE failures before giving up on SSE and polling instead. */
  maxReconnects?: number;
  /** Poll interval for the GET /runs/{id} fallback (ms). */
  pollIntervalMs?: number;
}

/**
 * Subscribe to a run's live state (plan #20). Loads the `GET /runs/{id}`
 * snapshot first, then opens the SSE stream via {@link openRunStream}, applying
 * `account` / `status` / `tool` events. On a dropped connection it reconnects
 * with a fixed delay up to `maxReconnects`; if SSE keeps failing it falls back
 * to polling `GET /runs/{id}`. Streaming/polling stop once the run is terminal.
 *
 * @param runId Run to subscribe to; an empty value disables the hook.
 * @returns The merged run/account/tool state plus the current transport mode.
 */
export function useRunStream(
  runId: string,
  options: UseRunStreamOptions = {},
): RunStreamState {
  const { reconnectDelayMs = 1500, maxReconnects = 3, pollIntervalMs = 4000 } = options;
  const [state, dispatch] = useReducer(reducer, initialState);

  // Keep the latest run status visible to async callbacks without re-subscribing.
  const statusRef = useRef<string | null>(null);
  statusRef.current = state.run?.status ?? null;

  useEffect(() => {
    if (!runId) return;

    let cancelled = false;
    let closeStream: (() => void) | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let failures = 0;

    const stopAll = () => {
      if (closeStream) {
        closeStream();
        closeStream = null;
      }
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const loadSnapshot = async (): Promise<boolean> => {
      try {
        const detail = await api.getRun(runId);
        if (cancelled) return false;
        dispatch({ kind: "snapshot", run: detail.run, accounts: detail.accounts });
        statusRef.current = detail.run.status ?? null;
        return true;
      } catch (err) {
        if (cancelled) return false;
        dispatch({
          kind: "snapshot-error",
          message: err instanceof Error ? err.message : "Failed to load run",
        });
        return false;
      }
    };

    const startPolling = () => {
      if (cancelled || pollTimer) return;
      dispatch({ kind: "mode", mode: "polling" });
      pollTimer = setInterval(async () => {
        const ok = await loadSnapshot();
        if (ok && isTerminal(statusRef.current)) {
          stopAll();
          dispatch({ kind: "mode", mode: "closed" });
        }
      }, pollIntervalMs);
    };

    const connect = () => {
      if (cancelled) return;
      dispatch({ kind: "mode", mode: failures === 0 ? "connecting" : "polling" });
      closeStream = openRunStream(runId, {
        onOpen: () => {
          failures = 0;
          dispatch({ kind: "mode", mode: "live" });
        },
        onEvent: (event: RunStreamEvent) => {
          if (cancelled) return;
          if (event.type === "account") dispatch({ kind: "account", event });
          else if (event.type === "status") {
            dispatch({ kind: "status", event });
            statusRef.current = event.status;
            if (isTerminal(event.status)) {
              stopAll();
              dispatch({ kind: "mode", mode: "closed" });
            }
          } else if (event.type === "tool") dispatch({ kind: "tool", event });
        },
        onError: () => {
          if (cancelled) return;
          // EventSource auto-reconnects, but if the run is terminal just stop.
          if (isTerminal(statusRef.current)) {
            stopAll();
            dispatch({ kind: "mode", mode: "closed" });
            return;
          }
          failures += 1;
          if (closeStream) {
            closeStream();
            closeStream = null;
          }
          if (failures >= maxReconnects) {
            // SSE keeps failing — degrade to GET /runs/{id} polling.
            startPolling();
            return;
          }
          reconnectTimer = setTimeout(connect, reconnectDelayMs);
        },
      });
    };

    void (async () => {
      const ok = await loadSnapshot();
      if (cancelled) return;
      if (ok && isTerminal(statusRef.current)) {
        // Already finished: snapshot is the whole story, no stream needed.
        dispatch({ kind: "mode", mode: "closed" });
        return;
      }
      connect();
    })();

    return () => {
      cancelled = true;
      stopAll();
    };
  }, [runId, reconnectDelayMs, maxReconnects, pollIntervalMs]);

  return state;
}
