import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";

import type { RunDetail, RunStreamEvent } from "@/lib/api";

// Mock the API boundary so the hook never hits the network (offline house rule).
const getRun = vi.fn();
const openRunStream = vi.fn();

vi.mock("@/lib/api", () => ({
  api: { getRun: (...a: unknown[]) => getRun(...a) },
  openRunStream: (...a: unknown[]) => openRunStream(...a),
}));

import { useRunStream } from "./useRunStream";

function snapshot(over: Partial<RunDetail["run"]> = {}): RunDetail {
  return {
    run: {
      run_id: "r1",
      run_date: "2026-06-06",
      started_at: "2026-06-06T10:00:00Z",
      finished_at: null,
      dry_run: 1,
      concurrency: 4,
      model: "claude",
      app_env: "dev",
      accounts_total: 1,
      accounts_succeeded: 0,
      accounts_failed: 0,
      status: "running",
      triggered_by: "me@x.com",
      ...over,
    },
    accounts: [
      {
        run_id: "r1",
        domain: "acme.com",
        company_name: "Acme Inc",
        country: "US",
        status: "pending",
        score: null,
        tier: null,
        confidence: null,
        needs_human_research: null,
        signals_count: null,
        signals_json: null,
        score_json: null,
        error: null,
        started_at: null,
        finished_at: null,
      },
    ],
  };
}

/** Capture the EventSource handlers openRunStream is called with so a test can drive them. */
function captureStream() {
  type Handlers = {
    onEvent: (e: RunStreamEvent) => void;
    onError?: (e: Event) => void;
    onOpen?: () => void;
  };
  const close = vi.fn();
  let handlers: Handlers | null = null;
  openRunStream.mockImplementation((_runId: string, h: Handlers) => {
    handlers = h;
    return close;
  });
  return {
    close,
    open: () => handlers?.onOpen?.(),
    emit: (e: RunStreamEvent) => handlers?.onEvent(e),
    fail: () => handlers?.onError?.(new Event("error")),
    get handlers() {
      return handlers;
    },
  };
}

beforeEach(() => {
  getRun.mockReset();
  openRunStream.mockReset();
});

describe("useRunStream", () => {
  it("replays the snapshot, then goes live and applies account/tool events", async () => {
    getRun.mockResolvedValue(snapshot());
    const stream = captureStream();

    const { result } = renderHook(() => useRunStream("r1"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.accounts[0].status).toBe("pending");
    expect(result.current.run?.run_id).toBe("r1");

    act(() => stream.open());
    expect(result.current.mode).toBe("live");

    act(() =>
      stream.emit({
        type: "account",
        run_id: "r1",
        account: "acme.com",
        status: "done",
        score: 8,
        tier: "Tier 1",
        confidence: "high",
        signals_count: 2,
        ts: "t1",
      }),
    );
    expect(result.current.accounts[0].status).toBe("done");
    expect(result.current.accounts[0].score).toBe(8);

    act(() =>
      stream.emit({
        type: "tool",
        run_id: "r1",
        account: "acme.com",
        agent: "scout",
        beat: "hiring",
        tool: "exa_search",
        arg_summary: "q=acme",
        ts: "t2",
      }),
    );
    expect(result.current.toolEvents).toHaveLength(1);
    expect(result.current.toolEvents[0].tool).toBe("exa_search");
  });

  it("closes the stream when a terminal status event arrives", async () => {
    getRun.mockResolvedValue(snapshot());
    const stream = captureStream();
    const { result } = renderHook(() => useRunStream("r1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => stream.open());

    act(() => stream.emit({ type: "status", run_id: "r1", status: "done", ts: "t3" }));

    expect(result.current.run?.status).toBe("done");
    expect(result.current.mode).toBe("closed");
    expect(stream.close).toHaveBeenCalled();
  });

  it("does not open a stream when the snapshot is already terminal", async () => {
    getRun.mockResolvedValue(snapshot({ status: "done" }));
    const { result } = renderHook(() => useRunStream("r1"));
    await waitFor(() => expect(result.current.mode).toBe("closed"));
    expect(openRunStream).not.toHaveBeenCalled();
  });

  it("falls back to polling after repeated SSE failures", async () => {
    vi.useFakeTimers();
    try {
      getRun.mockResolvedValue(snapshot());
      const stream = captureStream();
      const { result } = renderHook(() => useRunStream("r1", { maxReconnects: 1 }));

      // resolve the initial snapshot promise
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(getRun).toHaveBeenCalledTimes(1);

      // First failure with maxReconnects=1 triggers the polling fallback.
      act(() => stream.fail());
      expect(result.current.mode).toBe("polling");

      // Advance one poll interval -> a second GET /runs/{id}.
      await act(async () => {
        vi.advanceTimersByTime(4000);
        await Promise.resolve();
      });
      expect(getRun.mock.calls.length).toBeGreaterThanOrEqual(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("records a snapshot error and surfaces it", async () => {
    getRun.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useRunStream("r1"));
    await waitFor(() => expect(result.current.error).toBe("boom"));
    expect(result.current.loading).toBe(false);
  });

  it("is disabled (no fetch) when runId is empty", () => {
    renderHook(() => useRunStream(""));
    expect(getRun).not.toHaveBeenCalled();
    expect(openRunStream).not.toHaveBeenCalled();
  });
});
