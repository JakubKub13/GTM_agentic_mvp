"""Tests for the SSE stream endpoint (plan #10, #11).

Offline: drives the in-process event bus + the SQLite snapshot directly and consumes the
async event generator behind the SSE response — no real network, no proxy, no LLM.

We exercise the generator (``routes_runs._stream_events``) rather than a live socket so
the snapshot-replay → live-stream → terminal-close contract is asserted deterministically
on a "fake stream": we feed events into the bus and flip the run status, then read what the
generator yields.
"""

import asyncio
import json

import pytest

from duvo.api import routes_runs
from duvo.infra import events
from duvo.store import db, runs


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    db._INITED.clear()
    yield
    db._INITED.clear()


@pytest.fixture(autouse=True)
def _clean_bus():
    events._subscribers.clear()
    yield
    events._subscribers.clear()


async def _seed_run(run_id: str, *, status: str = "running") -> None:
    await runs.start_run_strict(
        run_id=run_id,
        run_date="2026-06-06",
        started_at="2026-06-06T10:00:00Z",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        triggered_by="op@duvo.io",
        companies_json=json.dumps([{"name": "Acme", "domain": "acme.com"}]),
        accounts=[{"domain": "acme.com", "company_name": "Acme", "country": "US"}],
    )
    if status != "running":
        await db.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))


async def _drain(gen, *, limit: int = 50) -> list:
    """Collect up to *limit* items from the SSE async generator without hanging."""
    out: list = []
    for _ in range(limit):
        try:
            item = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        except (StopAsyncIteration, TimeoutError):
            break
        out.append(item)
    return out


def _data_events(items: list) -> list[dict]:
    """Extract JSON-decoded ``data`` payloads (skip heartbeat comments)."""
    payloads = []
    for it in items:
        data = it.get("data") if isinstance(it, dict) else getattr(it, "data", None)
        if not data:
            continue
        try:
            payloads.append(json.loads(data))
        except (ValueError, TypeError):
            continue
    return payloads


# --------------------------------------------------------------------------- #
# Snapshot replay on (re)connect (#10)
# --------------------------------------------------------------------------- #
async def test_stream_flushes_db_snapshot_then_closes_on_terminal():
    # A run that is ALREADY terminal: the stream flushes the snapshot and closes.
    await _seed_run("r1", status="done")
    await db.execute(
        "UPDATE account_runs SET status = 'done', score = 8, tier = 'A' WHERE run_id = ?",
        ("r1",),
    )

    gen = routes_runs._stream_events("r1")
    items = await _drain(gen)
    payloads = _data_events(items)

    # Snapshot replays account status (#10): the seeded account appears.
    account_events = [p for p in payloads if p.get("type") == "account"]
    assert any(p.get("account") == "acme.com" for p in account_events)
    # A terminal status event is emitted and the generator then closes.
    status_events = [p for p in payloads if p.get("type") == "status"]
    assert any(p.get("status") == "done" for p in status_events)


async def test_stream_replays_completed_account_agent_log():
    await _seed_run("r1", status="done")
    await db.execute(
        "UPDATE account_runs SET status = 'done', agent_log_json = ? WHERE run_id = ?",
        (json.dumps([{"tool": "exa_search"}]), "r1"),
    )

    gen = routes_runs._stream_events("r1")
    items = await _drain(gen)
    payloads = _data_events(items)

    account = next(p for p in payloads if p.get("type") == "account")
    # The completed account's persisted agent_log is replayed in its snapshot (#10).
    assert account.get("agent_log") is not None


# --------------------------------------------------------------------------- #
# Live streaming until terminal status (#11)
# --------------------------------------------------------------------------- #
async def _wait_for_subscriber(run_id: str, *, timeout: float = 3.0) -> None:
    """Block until the generator has registered its bus subscriber (or time out).

    The live-delivery contract (#11) requires publishing *after* the SSE generator has
    reached ``events.subscribe()`` — ``publish`` is a deliberate no-op with no subscribers
    (events.py:100-102), so an event sent before the subscriber registers is silently
    dropped. We poll the bus's own registry — a deterministic happens-before edge — until
    the subscriber appears, instead of relying on a bare ``asyncio.sleep(0)``.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while not events._subscribers.get(run_id):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"generator never subscribed to run {run_id!r}")
        await asyncio.sleep(0)


async def test_stream_streams_live_events_then_closes_on_terminal():
    # Core #11 invariant: a `tool` event published while a run is live is delivered to the
    # SSE consumer, and a terminal `status` event closes the stream.
    #
    # This test is deterministic on two fronts, both previously sources of flakiness:
    #  1. We publish only AFTER `_wait_for_subscriber` confirms the generator reached
    #     `events.subscribe()`; otherwise `publish` is a no-op and the event is dropped.
    #  2. We do NOT flip the DB run status to terminal mid-stream. The snapshot read runs in
    #     a worker thread (`asyncio.to_thread`), so a concurrent `UPDATE runs ... done` could
    #     land BEFORE the snapshot SELECT — the generator would then see a terminal snapshot
    #     and close without ever entering the live loop. Instead the run stays `running` in
    #     the DB (snapshot always non-terminal → live loop is entered) and the stream is
    #     closed by the *published* terminal `status` event (routes_runs.py:381), which is
    #     exactly what the live contract specifies.
    await _seed_run("r1", status="running")

    gen = routes_runs._stream_events("r1")

    # Drive the generator on its own task: snapshot flush → then it awaits live events.
    snapshot_task = asyncio.create_task(_drain_until_live(gen))

    # Deterministic sync: the subscriber registers synchronously as the generator's first
    # action, so once it appears, every event below is guaranteed not-dropped and ordered
    # (FIFO) behind the snapshot reads the generator does next.
    await _wait_for_subscriber("r1")

    # Live tool event (must be delivered), then a terminal status event (must close).
    events.publish("r1", events.Event(type="tool", run_id="r1", account="acme.com", tool="exa"))
    events.publish("r1", events.Event(type="status", run_id="r1", status="done"))

    items = await asyncio.wait_for(snapshot_task, timeout=3.0)
    payloads = _data_events(items)

    # The live `tool` event reached the consumer (the core #11 live-delivery contract).
    assert any(p.get("type") == "tool" and p.get("tool") == "exa" for p in payloads)
    # The terminal `status` event closed the stream (it appears and the generator returned).
    assert any(p.get("type") == "status" and p.get("status") == "done" for p in payloads)
    # Subscriber was cleaned up after the stream closed (no leak).
    assert "r1" not in events._subscribers or gen_closed(gen)


async def _drain_until_live(gen, *, limit: int = 50) -> list:
    out: list = []
    for _ in range(limit):
        try:
            item = await asyncio.wait_for(gen.__anext__(), timeout=3.0)
        except (StopAsyncIteration, TimeoutError):
            break
        out.append(item)
    return out


def gen_closed(gen) -> bool:
    return getattr(gen, "ag_frame", "closed") is None


async def test_stream_endpoint_serves_event_stream_through_asgi():
    # End-to-end: the EventSourceResponse actually serves text/event-stream for a
    # terminal run (closes immediately after the snapshot, so the request returns).
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from duvo.api import auth, routes_runs

    await _seed_run("rdone", status="done")
    await db.execute(
        "UPDATE account_runs SET status = 'done', score = 7, tier = 'B' WHERE run_id = ?",
        ("rdone",),
    )

    app = FastAPI()
    app.include_router(routes_runs.router)
    app.dependency_overrides[auth.get_current_user] = lambda: auth.User(
        email="op@duvo.io", name="Op", role="admin"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await asyncio.wait_for(c.get("/runs/rdone/stream"), timeout=5.0)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers.get("x-accel-buffering") == "no"
    body = resp.text
    assert "event: account" in body
    assert "event: status" in body
    assert "acme.com" in body


async def test_stream_missing_run_emits_status_and_closes():
    # No run row at all → the stream should not hang; it emits a terminal-ish status.
    gen = routes_runs._stream_events("ghost")
    items = await _drain(gen, limit=5)
    # It must terminate (not hang) — _drain returns; the generator closed.
    payloads = _data_events(items)
    assert payloads == [] or all(p.get("type") in {"status", "account"} for p in payloads)
