"""Tests for the in-process job registry + done_callback (plan #8).

Fully offline: the only side effect is a row in a temp SQLite DB. No network.
"""

import asyncio

import pytest

from duvo.api import jobs
from duvo.store import db, runs


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    db._INITED.clear()
    yield
    db._INITED.clear()


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts and ends with an empty registry (no leaks across tests)."""
    jobs._REGISTRY.clear()
    jobs._FINALIZERS.clear()
    yield
    jobs._REGISTRY.clear()
    jobs._FINALIZERS.clear()


async def _seed_running_run(run_id: str) -> None:
    await runs.start_run(
        run_id=run_id,
        run_date="2026-06-06",
        started_at="2026-06-06T10:00:00Z",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=1,
    )


async def _status(run_id: str) -> str | None:
    row = await db.query_one("SELECT status FROM runs WHERE run_id = ?", (run_id,))
    return row["status"] if row is not None else None


# --- registration + de-registration ---


async def test_register_adds_task_and_done_callback_removes_it():
    async def _ok():
        return "fine"

    task = asyncio.create_task(_ok())
    jobs.register("r1", task)
    assert jobs.get("r1") is task
    assert "r1" in jobs.active_run_ids()

    await task
    await jobs.wait_for_finalizers()

    # The done_callback de-registers the finished task — no leak.
    assert jobs.get("r1") is None
    assert "r1" not in jobs.active_run_ids()


async def test_clean_completion_does_not_overwrite_terminal_status():
    # run() marks the run 'done' itself; the callback must NOT clobber that to 'failed'.
    await _seed_running_run("r1")
    await db.execute("UPDATE runs SET status = 'done' WHERE run_id = ?", ("r1",))

    async def _ok():
        return None

    task = asyncio.create_task(_ok())
    jobs.register("r1", task)
    await task
    await jobs.wait_for_finalizers()

    assert await _status("r1") == "done"


# --- exception handling: log + mark failed when run died non-terminal ---


async def test_unhandled_exception_marks_run_failed_and_logs(caplog):
    import logging

    await _seed_running_run("r1")  # left in 'running' (run() never reached a terminal state)

    async def _boom():
        raise RuntimeError("kaboom")

    task = asyncio.create_task(_boom())
    jobs.register("r1", task)

    # The duvo logger sets propagate=False, so caplog's root handler never sees the
    # record. Attach caplog's handler directly to the module logger for this assertion.
    jobs_logger = logging.getLogger("duvo.api.jobs")
    jobs_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level("ERROR", logger="duvo.api.jobs"):
            # Awaiting the task surfaces the exception here; the callback still ran.
            with pytest.raises(RuntimeError, match="kaboom"):
                await task
            await jobs.wait_for_finalizers()
    finally:
        jobs_logger.removeHandler(caplog.handler)

    assert await _status("r1") == "failed"
    assert jobs.get("r1") is None
    # The unhandled exception was logged (never swallowed) and tagged with the run_id.
    assert any(
        "r1" in r.getMessage() and r.levelno >= logging.ERROR for r in caplog.records
    )


async def test_exception_does_not_overwrite_already_terminal_status():
    # If run() already recorded 'interrupted' before raising, the callback must respect it.
    await _seed_running_run("r1")
    await db.execute("UPDATE runs SET status = 'interrupted' WHERE run_id = ?", ("r1",))

    async def _boom():
        raise RuntimeError("late")

    task = asyncio.create_task(_boom())
    jobs.register("r1", task)
    with pytest.raises(RuntimeError):
        await task
    await jobs.wait_for_finalizers()

    assert await _status("r1") == "interrupted"


# --- cancellation: mark interrupted when run died non-terminal ---


async def test_cancelled_task_marks_run_interrupted():
    await _seed_running_run("r1")

    async def _hang():
        await asyncio.sleep(3600)

    task = asyncio.create_task(_hang())
    jobs.register("r1", task)
    await asyncio.sleep(0)  # let it start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await jobs.wait_for_finalizers()

    assert await _status("r1") == "interrupted"
    assert jobs.get("r1") is None


# --- drain (shutdown #6) ---


async def test_drain_cancels_and_awaits_all_active_tasks():
    await _seed_running_run("r1")
    await _seed_running_run("r2")

    async def _hang():
        await asyncio.sleep(3600)

    t1 = asyncio.create_task(_hang())
    t2 = asyncio.create_task(_hang())
    jobs.register("r1", t1)
    jobs.register("r2", t2)
    await asyncio.sleep(0)

    await jobs.drain()
    await jobs.wait_for_finalizers()

    assert t1.cancelled()
    assert t2.cancelled()
    # Registry emptied; both runs surfaced as interrupted.
    assert jobs.active_run_ids() == []
    assert await _status("r1") == "interrupted"
    assert await _status("r2") == "interrupted"


async def test_drain_is_a_noop_with_no_active_tasks():
    # Must not raise when nothing is registered.
    await jobs.drain()
    assert jobs.active_run_ids() == []
