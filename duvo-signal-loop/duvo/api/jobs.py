"""In-process registry of running batch tasks (plan #8).

A long-lived API process launches each ``POST /runs`` as an ``asyncio.Task`` (the
"in-process asyncio task, NOT a task queue" decision). This module is the single
owner of those tasks:

* :func:`register` maps ``run_id -> Task`` and attaches a ``done_callback`` that
  **de-registers** the task, **logs** any unhandled exception (so it is never
  swallowed), and **marks the run terminal** (``failed`` / ``interrupted``) when the
  task died without ``run()`` having already reached a clean terminal status — so a
  crash or cancellation can never leave a row stuck in ``running``.
* :func:`drain` cancels and awaits every active task on shutdown (plan #6), so no
  task leaks past the process lifetime.

Single-process by design (see the plan's "Risks": ``uvicorn --workers 1``). The
registry is module-global, mirroring the other process-level singletons in the
codebase (``infra/tracing._client``, ``infra/http_client``).
"""

from __future__ import annotations

import asyncio

from duvo.infra.logging_setup import get_logger
from duvo.store import db

_log = get_logger(__name__)

# Statuses ``run()`` sets when it owns the outcome; the callback must not clobber them.
_TERMINAL_STATUSES = frozenset({"done", "interrupted", "cancelled", "failed"})

# run_id -> the live batch Task. Module-global: one API process (plan "Risks").
_REGISTRY: dict[str, asyncio.Task] = {}

# run_id -> the async finalizer Task spawned by a done_callback. Tracked so shutdown
# (and tests) can await the DB write that the sync callback can only *schedule*.
_FINALIZERS: dict[str, asyncio.Task] = {}


def register(run_id: str, task: asyncio.Task) -> None:
    """Register *task* under *run_id* and wire its terminal-state safety net.

    Attaches an ``add_done_callback`` that de-registers the task, logs any unhandled
    exception, and — when the task ended by exception or cancellation while the run is
    still non-terminal in the DB — schedules an async finalizer that marks the run
    ``failed`` (exception) or ``interrupted`` (cancelled). A clean completion, or a run
    that ``run()`` already marked terminal, is left untouched.

    Args:
        run_id: The run this task is executing.
        task: The ``asyncio.Task`` wrapping ``orchestrator.run(...)``.
    """
    _REGISTRY[run_id] = task

    def _on_done(t: asyncio.Task, _run_id: str = run_id) -> None:
        # Always de-register first so tasks never leak, even if logging below raises.
        _REGISTRY.pop(_run_id, None)

        cancelled = t.cancelled()
        exc: BaseException | None = None
        if not cancelled:
            exc = t.exception()
            if exc is not None:
                _log.error("run %s task died with unhandled exception", _run_id, exc_info=exc)

        if cancelled:
            _log.warning("run %s task was cancelled", _run_id)

        # Mark the run terminal only when it died unexpectedly. A clean return means
        # run() already recorded the outcome, so there is nothing to finalize.
        if cancelled or exc is not None:
            new_status = "interrupted" if cancelled else "failed"
            finalizer = asyncio.ensure_future(_finalize_run_status(_run_id, new_status))
            _FINALIZERS[_run_id] = finalizer
            finalizer.add_done_callback(lambda _f, rid=_run_id: _FINALIZERS.pop(rid, None))

    task.add_done_callback(_on_done)


async def _finalize_run_status(run_id: str, new_status: str) -> None:
    """Mark *run_id* terminal as *new_status*, but only if it is still non-terminal.

    ``run()`` owns the happy path and the cooperative-cancellation path (it sets
    ``done`` / ``interrupted`` itself). This is the safety net for the case where the
    task died *before* ``run()`` could record an outcome (e.g. an exception escaping the
    pipeline, or a hard cancel), leaving the row stuck in ``running``. We re-read the
    persisted status to avoid racing/overwriting an already-recorded terminal state.

    Args:
        run_id: The run to finalize.
        new_status: ``failed`` (task raised) or ``interrupted`` (task cancelled).
    """
    row = await db.query_one("SELECT status FROM runs WHERE run_id = ?", (run_id,))
    if row is None:
        # No persisted run (e.g. a never-persisted/aborted launch) — nothing to mark.
        return
    if row["status"] in _TERMINAL_STATUSES:
        return
    await db.execute(
        "UPDATE runs SET status = ? WHERE run_id = ?",
        (new_status, run_id),
    )
    _log.info("run %s marked %s by done_callback", run_id, new_status)


def get(run_id: str) -> asyncio.Task | None:
    """Return the active task for *run_id*, or ``None`` if not registered."""
    return _REGISTRY.get(run_id)


def active_run_ids() -> list[str]:
    """Return the run_ids of all currently-registered (active) tasks."""
    return list(_REGISTRY)


async def drain() -> None:
    """Cancel and await every active task — the shutdown sweep (plan #6).

    Each task's ``done_callback`` de-registers it and surfaces it as ``interrupted``
    (cancellation path), so after this returns the registry is empty and no task leaks
    past process shutdown. A no-op when nothing is registered.
    """
    tasks = list(_REGISTRY.values())
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    # return_exceptions=True so one task's error/cancel can't abort the drain.
    await asyncio.gather(*tasks, return_exceptions=True)


async def wait_for_finalizers() -> None:
    """Await any in-flight finalizer tasks spawned by done_callbacks.

    A ``done_callback`` is synchronous and can only *schedule* the async DB write that
    marks a crashed/cancelled run terminal. Shutdown (and tests) call this so that
    pending status updates have committed before the process moves on.
    """
    # Yield once so any done_callbacks already queued via call_soon (which spawn the
    # finalizers) have run before we snapshot the set.
    await asyncio.sleep(0)
    finalizers = list(_FINALIZERS.values())
    if not finalizers:
        return
    await asyncio.gather(*finalizers, return_exceptions=True)
