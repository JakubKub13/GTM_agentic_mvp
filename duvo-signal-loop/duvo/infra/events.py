"""In-process event bus for the live run feed — an optional boundary.

Mirrors :mod:`duvo.infra.tracing`'s on/off pattern: with no subscribers,
:func:`publish` is a no-op, so the CLI path and the offline test suite are
completely unaffected. Subscribers (the FastAPI SSE endpoint) register a
**bounded** :class:`asyncio.Queue` per ``run_id``; on a full queue ``publish``
**drops** the event rather than awaiting the producer — there is no backpressure
into the agent pipeline.

Producers thread ``run_id`` + structured context through a :class:`contextvar`
(set by ``_process_account`` / ``run_scout`` / ``run_analyst`` / ``run_router``),
so no agent signature changes. ``asyncio.gather`` copies the context per task,
keeping tags isolated across interleaved runs.

Event payloads are **bounded and redacted**: a ``tool`` event never carries raw
``tc.arguments`` (which include full outreach drafts + reasoning). It reuses
``agent_core._short`` truncation plus ``tracing._mask`` email masking.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from typing import Any, TypedDict

from duvo.infra import tracing
from duvo.infra.logging_setup import get_logger

_log = get_logger(__name__)

# Default per-subscriber queue depth. Bounded so a slow/stalled SSE client can
# never grow memory without limit; surplus events are dropped, not buffered.
DEFAULT_MAXSIZE = 1000

# run_id -> set of subscriber queues. Single-threaded asyncio: plain dict/set
# mutation is atomic enough; no lock needed.
_subscribers: dict[str, set[asyncio.Queue[Event]]] = {}

# Structured producer context, copied per task by asyncio.gather. Holds run_id +
# account/agent/beat tags so publish() can stamp events without signature changes.
_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "duvo_event_context", default={}
)


class Event(TypedDict, total=False):
    """A bounded, redacted feed event.

    Keys are optional (``total=False``) so producers stamp only what they know:
    ``type`` and ``run_id`` are always present; the rest are context-dependent.
    """

    type: str  # "tool" | "account" | "status"
    run_id: str
    account: str | None
    agent: str | None
    beat: str | None
    tool: str | None
    arg_summary: str | None
    # Account / status fields (live AccountsTable + RunHeader, plan #20).
    status: str | None
    score: int | None
    tier: str | None
    confidence: str | None
    signals_count: int | None
    agent_log: Any
    ts: float


def subscribe(run_id: str, maxsize: int = DEFAULT_MAXSIZE) -> asyncio.Queue[Event]:
    """Register and return a bounded subscriber queue for *run_id*.

    Args:
        run_id:  The run to receive events for.
        maxsize: Queue depth; surplus events are dropped on a full queue.

    Returns:
        The newly created :class:`asyncio.Queue` to consume events from.
    """
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
    _subscribers.setdefault(run_id, set()).add(queue)
    return queue


def unsubscribe(run_id: str, queue: asyncio.Queue[Event]) -> None:
    """Remove *queue* from *run_id*'s subscribers, cleaning up the empty key.

    Never raises if the queue or run_id is already gone.
    """
    subs = _subscribers.get(run_id)
    if subs is None:
        return
    subs.discard(queue)
    if not subs:
        _subscribers.pop(run_id, None)


def publish(run_id: str, event: Event) -> None:
    """Fan *event* out to every subscriber of *run_id* — never blocks.

    A no-op when no one is subscribed (the CLI / offline-test guarantee). On a
    full queue the event is **dropped** (``put_nowait`` raises ``QueueFull``,
    which is swallowed) so the producer is never awaited and no backpressure
    reaches the agent pipeline. Never raises.
    """
    subs = _subscribers.get(run_id)
    if not subs:
        return
    for queue in subs:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            _log.debug("event dropped (queue full) run_id=%s type=%s", run_id, event.get("type"))
        except Exception as exc:  # pragma: no cover - defensive
            _log.debug("event publish failed run_id=%s: %s", run_id, exc)


# ---------------------------------------------------------------------------
# Producer context (contextvar) helpers
# ---------------------------------------------------------------------------


def set_context(*, run_id: str, account: str | None = None) -> None:
    """Start a fresh producer context for the current task.

    Replaces (does not merge) the context, so a reused worker task can't inherit
    a previous account's tags. Call this at the top of ``_process_account``.
    """
    _context.set({"run_id": run_id, "account": account})


def enrich_context(**fields: Any) -> None:
    """Merge *fields* (e.g. ``agent=``, ``beat=``) into the current context.

    Copies before mutating so a child task that inherited the parent's context
    dict via ``asyncio.gather`` does not retroactively edit the parent's tags.
    """
    current = dict(_context.get())
    current.update(fields)
    _context.set(current)


def get_context() -> dict[str, Any]:
    """Return a copy of the current producer context."""
    return dict(_context.get())


# ---------------------------------------------------------------------------
# Typed event builders (bounded + redacted)
# ---------------------------------------------------------------------------


def make_tool_event(*, tool: str, arguments: dict[str, Any]) -> Event:
    """Build a redacted ``tool`` event from the current producer context.

    The argument summary is bounded and email-masked: it runs the tool
    arguments through ``agent_core._short`` (≤ 120 chars, each value clipped to
    40) and then ``tracing._mask`` to redact emails. Raw ``arguments`` (full
    outreach drafts + reasoning) never enter the payload.

    Args:
        tool:      The tool name the agent called.
        arguments: The tool-call arguments dict (never stored raw).

    Returns:
        A redacted :class:`Event` stamped with the current context tags.
    """
    # Imported here (not at module top) to avoid an import cycle: agent_core
    # imports duvo.infra.events to publish at its tool-call site.
    from duvo.agent_core import _short

    arg_summary = tracing._mask(_short(arguments))
    ctx = _context.get()
    return Event(
        type="tool",
        run_id=ctx.get("run_id", ""),
        account=ctx.get("account"),
        agent=ctx.get("agent"),
        beat=ctx.get("beat"),
        tool=tool,
        arg_summary=arg_summary,
        ts=time.time(),
    )


def make_account_event(
    *,
    status: str,
    score: int | None = None,
    tier: str | None = None,
    confidence: str | None = None,
    signals_count: int | None = None,
) -> Event:
    """Build an ``account`` status event from the current producer context (plan #20).

    Stamped with ``run_id`` + ``account`` from the contextvar (set by ``_process_account``),
    so the live ``AccountsTable`` can flip a row ``pending → running → terminal`` over SSE
    without the producer threading any identifiers. The optional result fields are carried on
    a terminal (``done``) event so the row shows its score/tier/confidence/signal-count live.

    Args:
        status: The account's new status (``running`` | ``done`` | ``failed`` | ``cancelled``).
        score: Final ICP score (terminal events only).
        tier: Final tier (terminal events only).
        confidence: Final confidence (terminal events only).
        signals_count: Number of signals gathered (terminal events only).

    Returns:
        An :class:`Event` of type ``account`` stamped with the current context.
    """
    ctx = _context.get()
    return Event(
        type="account",
        run_id=ctx.get("run_id", ""),
        account=ctx.get("account"),
        status=status,
        score=score,
        tier=tier,
        confidence=confidence,
        signals_count=signals_count,
        ts=time.time(),
    )


def make_status_event(*, run_id: str, status: str) -> Event:
    """Build a run-level ``status`` event (plan #20: ``RunHeader`` + SSE terminal close).

    Args:
        run_id: The run whose status changed.
        status: The run's new status (e.g. ``done`` | ``interrupted``).

    Returns:
        An :class:`Event` of type ``status``.
    """
    return Event(type="status", run_id=run_id, status=status, ts=time.time())
