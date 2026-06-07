"""Tests for the event-bus wiring (plan #9 + #17).

Covers:
  * Context enrichment at each pipeline layer (run_id/account in _process_account,
    agent='scout'+beat in run_scout, role in run_analyst/run_router).
  * agent_core.run_agent publishes a redacted ``tool`` event at its tool-call site,
    stamped with the current contextvar tags, never carrying raw tc.arguments.
  * ``asyncio.gather`` keeps the contextvar isolated across interleaved tasks.
  * The process-wide global account semaphore (DUVO_GLOBAL_MAX_ACCOUNTS) bounds total
    concurrent accounts across runs, on top of the per-run semaphore.

Fully offline: no network, no real LLM/Exa — the model loop is faked the same way
the rest of the suite fakes it.
"""

import asyncio
from unittest.mock import patch

from duvo.infra import events
from tests.conftest import FakeLLMProvider, make_llm_response, make_tool_call


def _patch_provider(provider):
    return patch("duvo.agent_core.get_llm_provider", return_value=provider)


# ---------------------------------------------------------------------------
# Typed account/status event builders (plan #20 live feed)
# ---------------------------------------------------------------------------


class TestAccountAndStatusEventBuilders:
    def test_make_account_event_stamps_context_and_fields(self):
        events.set_context(run_id="run-Z", account="acme.com")
        ev = events.make_account_event(
            status="done", score=8, tier="Tier 1", confidence="high", signals_count=3
        )
        assert ev["type"] == "account"
        assert ev["run_id"] == "run-Z"
        assert ev["account"] == "acme.com"
        assert ev["status"] == "done"
        assert ev["score"] == 8
        assert ev["tier"] == "Tier 1"
        assert ev["confidence"] == "high"
        assert ev["signals_count"] == 3
        assert "ts" in ev

    def test_make_account_event_running_carries_no_result_fields(self):
        events.set_context(run_id="run-Z", account="acme.com")
        ev = events.make_account_event(status="running")
        assert ev["status"] == "running"
        assert ev["score"] is None and ev["tier"] is None

    def test_make_status_event_shape(self):
        ev = events.make_status_event(run_id="run-Z", status="done")
        assert ev["type"] == "status"
        assert ev["run_id"] == "run-Z"
        assert ev["status"] == "done"
        assert "ts" in ev


def _drain(queue: asyncio.Queue) -> list[events.Event]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


# ---------------------------------------------------------------------------
# agent_core publishes a redacted tool event at the tool-call site (#9)
# ---------------------------------------------------------------------------


class TestAgentCorePublishesToolEvents:
    async def test_tool_call_emits_a_tool_event_to_subscribers(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("exa_search", {"query": "acme news"}, "tc-1")],
                    stop_reason="tool_calls",
                ),
                make_llm_response(content="done"),
            ]
        )
        events.set_context(run_id="run-A", account="acme.com")
        events.enrich_context(agent="scout", beat="hiring")
        queue = events.subscribe("run-A")
        try:
            with _patch_provider(provider):
                from duvo.agent_core import run_agent

                await run_agent("sys", "go", [], impls={"exa_search": lambda query: "r"})
        finally:
            events.unsubscribe("run-A", queue)

        emitted = [e for e in _drain(queue) if e["type"] == "tool"]
        assert len(emitted) == 1
        ev = emitted[0]
        assert ev["run_id"] == "run-A"
        assert ev["account"] == "acme.com"
        assert ev["agent"] == "scout"
        assert ev["beat"] == "hiring"
        assert ev["tool"] == "exa_search"
        assert "query=" in ev["arg_summary"]
        assert "acme news" in ev["arg_summary"]

    async def test_no_subscribers_is_a_noop(self):
        # No subscribe() call — publish must be a silent no-op (CLI / offline guarantee).
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("exa_search", {"query": "q"}, "tc-x")],
                    stop_reason="tool_calls",
                ),
                make_llm_response(content="done"),
            ]
        )
        events.set_context(run_id="run-nobody", account="x.com")
        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            messages = await run_agent("sys", "go", [], impls={"exa_search": lambda query: "r"})
        # The loop completed normally; nothing raised.
        assert any(m.role == "tool" for m in messages)

    async def test_payload_is_redacted_never_raw_arguments(self):
        # Email in the args must be masked; long values truncated (via _short).
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[
                        make_tool_call(
                            "outreach_queue",
                            {"email": "ceo@acme.com", "body": "X" * 500},
                            "tc-r",
                        )
                    ],
                    stop_reason="tool_calls",
                ),
                make_llm_response(content="done"),
            ]
        )
        events.set_context(run_id="run-R", account="acme.com")
        queue = events.subscribe("run-R")
        try:
            with _patch_provider(provider):
                from duvo.agent_core import run_agent

                await run_agent("sys", "go", [], impls={"outreach_queue": lambda **k: "ok"})
        finally:
            events.unsubscribe("run-R", queue)

        ev = [e for e in _drain(queue) if e["type"] == "tool"][0]
        assert "ceo@acme.com" not in ev["arg_summary"]
        assert "[email]" in ev["arg_summary"]
        assert len(ev["arg_summary"]) <= 120
        assert "X" * 500 not in ev["arg_summary"]


# ---------------------------------------------------------------------------
# Context enrichment at the agent layers (#9)
# ---------------------------------------------------------------------------


class TestAgentLayerEnrichment:
    async def test_run_scout_enriches_agent_and_beat(self):
        from duvo.agents.scouts import scouts

        captured = {}

        async def fake_run_agent(system, user, tools, impls, **kw):
            captured.update(events.get_context())
            return []

        events.set_context(run_id="run-S", account="acme.com")
        with patch.object(scouts, "run_agent", fake_run_agent):
            from duvo.models import Company

            company = Company(name="Acme", domain="acme.com", country="DE", description="x")
            await scouts.run_scout(company, "hiring", "Hiring beat desc")

        assert captured["agent"] == "scout"
        assert captured["beat"] == "hiring"
        assert captured["run_id"] == "run-S"
        assert captured["account"] == "acme.com"

    async def test_run_analyst_enriches_role(self):
        from duvo.agents.analyst import analyst

        captured = {}

        async def fake_run_agent(system, user, tools, impls, **kw):
            captured.update(events.get_context())
            impls["record_assessment"](
                score=7,
                tier="Tier 1",
                confidence="high",
                why_fit=["a"],
                why_not=["b"],
                recommended_persona="VP",
                recommended_angle="cost",
                reasoning="ok",
                needs_human_research=False,
                outreach={"persona": "VP", "subject": "s", "first_line": "f", "body": "b"},
            )
            return []

        events.set_context(run_id="run-AN", account="acme.com")
        with patch.object(analyst, "run_agent", fake_run_agent):
            from duvo.models import Company

            company = Company(name="Acme", domain="acme.com", country="DE", description="x")
            await analyst.run_analyst(company, [])

        assert captured["agent"] == "analyst"
        assert captured["run_id"] == "run-AN"

    async def test_run_router_enriches_role(self):
        from duvo.agents.router import router
        from duvo.models import RunResult
        from tests.conftest import make_score

        captured = {}

        async def fake_run_agent(system, user, tools, impls, **kw):
            captured.update(events.get_context())
            return []

        events.set_context(run_id="run-RT", account="acme.com")
        rr = RunResult(score=make_score(), signals=[])
        with patch.object(router, "run_agent", fake_run_agent):
            await router.run_router(rr, dry_run=True, test_email="t@example.com")

        assert captured["agent"] == "router"
        assert captured["run_id"] == "run-RT"


# ---------------------------------------------------------------------------
# contextvar isolation under asyncio.gather (#9 / risk)
# ---------------------------------------------------------------------------


class TestGatherContextIsolation:
    async def test_interleaved_tasks_keep_isolated_tags(self):
        started = asyncio.Event()
        release = asyncio.Event()
        results: dict[str, dict] = {}

        async def worker(run_id: str, account: str, beat: str, first: bool):
            events.set_context(run_id=run_id, account=account)
            if first:
                started.set()
                await release.wait()  # hand control to the other task mid-flight
            events.enrich_context(agent="scout", beat=beat)
            if not first:
                release.set()  # let the first task resume after we mutate our own ctx
            results[run_id] = events.get_context()

        await asyncio.gather(
            worker("run-1", "a.com", "hiring", first=True),
            worker("run-2", "b.com", "funding", first=False),
        )

        # Each task's context is independent despite interleaving.
        assert results["run-1"] == {
            "run_id": "run-1",
            "account": "a.com",
            "agent": "scout",
            "beat": "hiring",
        }
        assert results["run-2"] == {
            "run_id": "run-2",
            "account": "b.com",
            "agent": "scout",
            "beat": "funding",
        }


# ---------------------------------------------------------------------------
# Process-wide global account semaphore (#17)
# ---------------------------------------------------------------------------


class TestGlobalAccountSemaphore:
    async def test_global_semaphore_bounds_total_concurrency_across_runs(self, monkeypatch):
        from duvo import orchestrator

        monkeypatch.setattr(orchestrator.config, "DUVO_GLOBAL_MAX_ACCOUNTS", 2, raising=False)
        # Reset the cached semaphore so the new cap takes effect on this loop.
        orchestrator._reset_global_account_semaphore()

        live = 0
        peak = 0
        gate = asyncio.Event()

        async def body():
            nonlocal live, peak
            sem = orchestrator._global_account_semaphore()
            async with sem:
                live += 1
                peak = max(peak, live)
                # Hold the slot until enough tasks have piled up to prove the bound.
                await gate.wait()
                live -= 1

        async def releaser():
            # Give all tasks a chance to contend, then release.
            for _ in range(20):
                await asyncio.sleep(0)
            gate.set()

        await asyncio.gather(*[body() for _ in range(6)], releaser())
        assert peak <= 2

    async def test_global_semaphore_is_a_singleton_per_loop(self):
        from duvo import orchestrator

        orchestrator._reset_global_account_semaphore()
        a = orchestrator._global_account_semaphore()
        b = orchestrator._global_account_semaphore()
        assert a is b
