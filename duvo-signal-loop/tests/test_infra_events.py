"""Tests for the in-process event bus — optional boundary, bounded, redacted.

Mirrors the tracing switch's guarantees: a no-op with no subscribers (CLI +
offline tests unaffected), never blocks the producer on a full queue, isolates
per-task context under ``asyncio.gather``, and never leaks raw tool arguments.
"""

import asyncio

import pytest

from duvo.infra import events


@pytest.fixture(autouse=True)
def _clean_bus():
    """Each test starts with an empty subscriber registry and reset contextvar."""
    events._subscribers.clear()
    token = events._context.set({})
    yield
    events._context.reset(token)
    events._subscribers.clear()


# ---------------------------------------------------------------------------
# No-subscriber no-op
# ---------------------------------------------------------------------------


def test_publish_is_noop_without_subscribers():
    # No subscribers registered for this run_id -> publish must not raise.
    events.publish("run-x", {"type": "tool", "run_id": "run-x"})
    assert "run-x" not in events._subscribers


async def test_subscribe_receives_published_event():
    q = events.subscribe("run-1")
    events.publish("run-1", {"type": "status", "run_id": "run-1", "agent": "scout"})
    got = await asyncio.wait_for(q.get(), timeout=1.0)
    assert got["type"] == "status"
    assert got["agent"] == "scout"


def test_unsubscribe_removes_queue_and_run_key():
    q = events.subscribe("run-2")
    assert "run-2" in events._subscribers
    events.unsubscribe("run-2", q)
    # Last subscriber gone -> run_id key cleaned up, so publish is a no-op again.
    assert "run-2" not in events._subscribers


def test_publish_isolated_per_run_id():
    q1 = events.subscribe("run-a")
    q2 = events.subscribe("run-b")
    events.publish("run-a", {"type": "tool", "run_id": "run-a"})
    assert q1.qsize() == 1
    assert q2.qsize() == 0


# ---------------------------------------------------------------------------
# Bounded queue — drop on full, never block the producer
# ---------------------------------------------------------------------------


def test_publish_drops_on_full_queue_without_blocking():
    q = events.subscribe("run-full", maxsize=2)
    # Publish more than maxsize from a synchronous (producer) context.
    for i in range(10):
        events.publish("run-full", {"type": "tool", "run_id": "run-full", "tool": str(i)})
    # Never blocked; queue is capped at maxsize, surplus dropped/coalesced.
    assert q.qsize() <= 2
    assert q.full()


async def test_publish_never_awaits_producer_on_full_queue():
    q = events.subscribe("run-block", maxsize=1)
    events.publish("run-block", {"type": "tool", "run_id": "run-block"})
    assert q.full()

    # If publish blocked, this would hang; wrap in wait_for to fail fast instead.
    async def _produce():
        events.publish("run-block", {"type": "tool", "run_id": "run-block"})

    await asyncio.wait_for(_produce(), timeout=1.0)
    assert q.qsize() == 1


# ---------------------------------------------------------------------------
# contextvar isolation under asyncio.gather
# ---------------------------------------------------------------------------


async def test_contextvar_isolation_under_gather():
    seen: dict[str, dict] = {}

    async def task(run_id: str, account: str):
        events.set_context(run_id=run_id, account=account)
        await asyncio.sleep(0)  # force interleaving
        events.enrich_context(agent="scout", beat="news")
        await asyncio.sleep(0)
        seen[run_id] = events.get_context()

    await asyncio.gather(
        task("r1", "acme.com"),
        task("r2", "globex.com"),
    )

    assert seen["r1"]["run_id"] == "r1"
    assert seen["r1"]["account"] == "acme.com"
    assert seen["r2"]["run_id"] == "r2"
    assert seen["r2"]["account"] == "globex.com"
    # Enrichment from one task must not bleed into the other.
    assert seen["r1"]["agent"] == "scout"
    assert seen["r2"]["agent"] == "scout"


# ---------------------------------------------------------------------------
# Redaction — never raw tc.arguments
# ---------------------------------------------------------------------------


def test_make_tool_event_redacts_email_and_truncates():
    long_draft = "x" * 500
    event = events.make_tool_event(
        tool="record_assessment",
        arguments={"email": "jane.doe@acme.com", "draft": long_draft},
    )
    assert event["type"] == "tool"
    assert event["tool"] == "record_assessment"
    summary = event["arg_summary"]
    # Email masked via tracing._mask.
    assert "jane.doe@acme.com" not in summary
    assert "[email]" in summary
    # _short truncation: never the full 500-char draft.
    assert long_draft not in summary
    assert len(summary) <= 120


def test_make_tool_event_pulls_context_fields():
    events.set_context(run_id="r9", account="acme.com")
    events.enrich_context(agent="analyst", beat=None)
    event = events.make_tool_event(tool="finish", arguments={})
    assert event["run_id"] == "r9"
    assert event["account"] == "acme.com"
    assert event["agent"] == "analyst"
    assert event["tool"] == "finish"
    assert isinstance(event["ts"], float)
