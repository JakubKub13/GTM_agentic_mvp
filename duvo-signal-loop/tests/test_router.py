"""Tests for router.py — the GTM routing agent.

Strategy: patch ``router.run_agent`` with a configurable fake that calls a sequence
of tool names from the ``impls`` dict directly, simulating the agent's decisions
without touching the Anthropic API.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import config
from models import RunResult
from tests.conftest import make_score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run_result(**kwargs) -> RunResult:
    """Build a RunResult with optional ICPScore overrides."""
    score = make_score(**kwargs)
    return RunResult(score=score, signals=[])


def _fake_run_agent_factory(tool_sequence: list[str]):
    """Return a fake ``run_agent`` that calls the given tool names in order.

    The fake accepts the same signature as the real ``run_agent`` and invokes
    ``impls[name]()`` for each name in *tool_sequence*.  This simulates the
    agent deciding which tools to call without any real Anthropic API calls.
    """
    def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
        for name in tool_sequence:
            if name in impls:
                result = impls[name]()
                if log is not None:
                    log.append(f"{name}()")
        return []  # router ignores the return value

    return fake_run_agent


# ---------------------------------------------------------------------------
# Import router after helpers to avoid triggering real Anthropic imports
# ---------------------------------------------------------------------------

import router  # noqa: E402 — must be after helpers


# ---------------------------------------------------------------------------
# Test: _tool_schema shape
# ---------------------------------------------------------------------------

class TestToolSchema:
    """_tool_schema returns correctly-shaped dicts."""

    def test_has_name_and_description(self):
        schema = router._tool_schema("my_tool", "does stuff")
        assert schema["name"] == "my_tool"
        assert schema["description"] == "does stuff"

    def test_has_empty_input_schema(self):
        schema = router._tool_schema("t", "desc")
        assert schema["input_schema"] == {"type": "object", "properties": {}}

    def test_four_tools_registered(self):
        """All four expected tool names appear in ROUTER_SYSTEM or are reachable via run_router."""
        # Verify ROUTER_SYSTEM is a non-empty string (public symbol)
        assert isinstance(router.ROUTER_SYSTEM, str)
        assert len(router.ROUTER_SYSTEM) > 20

    def test_tool_names_are_correct(self):
        expected_names = {"crm_upsert", "slack_alert", "outreach_queue", "finish"}
        # Reconstruct tool list by looking at what run_router would register.
        # We do this by running with a no-op fake and inspecting the tools arg.
        rr = _make_run_result()
        captured = {}

        def capture_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            captured["tool_names"] = {t["name"] for t in tools}
            captured["impl_names"] = set(impls.keys())

        with patch("router.run_agent", side_effect=capture_run_agent):
            router.run_router(rr, dry_run=True)

        assert captured["tool_names"] == expected_names
        assert captured["impl_names"] == expected_names


# ---------------------------------------------------------------------------
# Test: dry-run confident Tier-1
# ---------------------------------------------------------------------------

class TestDryRunConfidentTier1:
    """All three write-backs produce [dry-run] strings; no real adapters called."""

    def test_crm_status_dry_run_string(self, monkeypatch):
        monkeypatch.setattr(config, "CRM_PROVIDER", "attio")
        rr = _make_run_result(tier="Tier 1", needs_human_research=False)

        with patch("router.run_agent", side_effect=_fake_run_agent_factory(
                ["crm_upsert", "slack_alert", "outreach_queue", "finish"])):
            router.run_router(rr, dry_run=True)

        assert "[dry-run]" in rr.crm_status
        assert "attio" in rr.crm_status
        assert str(rr.score.score) in rr.crm_status  # ICP score appears in string

    def test_slack_status_dry_run_string(self, monkeypatch):
        rr = _make_run_result(tier="Tier 1", needs_human_research=False)

        with patch("router.run_agent", side_effect=_fake_run_agent_factory(
                ["crm_upsert", "slack_alert", "outreach_queue", "finish"])):
            router.run_router(rr, dry_run=True)

        assert "[dry-run]" in rr.slack_status

    def test_outreach_status_dry_run_string(self, monkeypatch):
        monkeypatch.setattr(config, "OUTREACH_PROVIDER", "brevo")
        rr = _make_run_result(tier="Tier 1", needs_human_research=False)

        with patch("router.run_agent", side_effect=_fake_run_agent_factory(
                ["crm_upsert", "slack_alert", "outreach_queue", "finish"])):
            router.run_router(rr, dry_run=True)

        assert "[dry-run]" in rr.outreach_status
        assert "brevo" in rr.outreach_status

    def test_no_real_crm_called_in_dry_run(self, monkeypatch):
        """writeback.crm.upsert_account must NOT be called in dry-run mode."""
        mock_upsert = MagicMock(return_value="should not be used")
        rr = _make_run_result(tier="Tier 1", needs_human_research=False)

        with patch("router.run_agent", side_effect=_fake_run_agent_factory(["crm_upsert"])), \
             patch("writeback.crm.upsert_account", mock_upsert):
            router.run_router(rr, dry_run=True)

        mock_upsert.assert_not_called()

    def test_provider_names_reflect_monkeypatched_values(self, monkeypatch):
        """dry-run status strings read CRM/OUTREACH_PROVIDER at call time."""
        monkeypatch.setattr(config, "CRM_PROVIDER", "hubspot")
        monkeypatch.setattr(config, "OUTREACH_PROVIDER", "lemlist")
        rr = _make_run_result(tier="Tier 1", needs_human_research=False)

        with patch("router.run_agent", side_effect=_fake_run_agent_factory(
                ["crm_upsert", "outreach_queue"])):
            router.run_router(rr, dry_run=True)

        assert "hubspot" in rr.crm_status
        assert "lemlist" in rr.outreach_status


# ---------------------------------------------------------------------------
# Test: safety guards — non-confident-Tier-1
# ---------------------------------------------------------------------------

class TestSafetyGuards:
    """slack_alert and outreach_queue refuse when not a confident Tier 1."""

    @pytest.mark.parametrize("tier,needs_human_research", [
        ("Tier 1", True),   # confident=False because needs_human_research
        ("Tier 2", False),  # not Tier 1
        ("Tier 3", False),  # not Tier 1
    ])
    def test_slack_alert_refuses(self, tier, needs_human_research):
        rr = _make_run_result(tier=tier, needs_human_research=needs_human_research)
        captured_return = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            result = impls["slack_alert"]()
            captured_return["slack"] = result

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=True)

        assert "refused" in captured_return["slack"]
        assert "not a confident Tier 1" in captured_return["slack"]
        # Status field must NOT have been updated
        assert rr.slack_status == "skipped"

    @pytest.mark.parametrize("tier,needs_human_research", [
        ("Tier 1", True),
        ("Tier 2", False),
        ("Tier 3", False),
    ])
    def test_outreach_queue_refuses(self, tier, needs_human_research):
        rr = _make_run_result(tier=tier, needs_human_research=needs_human_research)
        captured_return = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            result = impls["outreach_queue"]()
            captured_return["outreach"] = result

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=True)

        assert "refused" in captured_return["outreach"]
        assert "not a confident Tier 1" in captured_return["outreach"]
        assert rr.outreach_status == "skipped"

    def test_crm_always_allowed_for_non_tier1(self):
        """crm_upsert has no tier guard and succeeds for any tier in dry-run."""
        rr = _make_run_result(tier="Tier 2", needs_human_research=False)

        with patch("router.run_agent", side_effect=_fake_run_agent_factory(["crm_upsert"])):
            router.run_router(rr, dry_run=True)

        assert "[dry-run]" in rr.crm_status


# ---------------------------------------------------------------------------
# Test: confident_t1 logic
# ---------------------------------------------------------------------------

class TestConfidentT1Logic:
    """Verify the boolean gate: tier=='Tier 1' AND needs_human_research==False."""

    def test_tier1_no_research_tools_allowed(self):
        rr = _make_run_result(tier="Tier 1", needs_human_research=False)
        captured = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            captured["slack"] = impls["slack_alert"]()
            captured["outreach"] = impls["outreach_queue"]()

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=True)

        # Both should NOT be refused
        assert "refused" not in captured["slack"]
        assert "refused" not in captured["outreach"]

    def test_tier1_with_research_flag_refuses(self):
        rr = _make_run_result(tier="Tier 1", needs_human_research=True)
        captured = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            captured["slack"] = impls["slack_alert"]()
            captured["outreach"] = impls["outreach_queue"]()

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=True)

        assert "refused" in captured["slack"]
        assert "refused" in captured["outreach"]

    def test_tier2_refuses_both_guarded_tools(self):
        rr = _make_run_result(tier="Tier 2", needs_human_research=False)
        captured = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            captured["slack"] = impls["slack_alert"]()
            captured["outreach"] = impls["outreach_queue"]()

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=True)

        assert "refused" in captured["slack"]
        assert "refused" in captured["outreach"]


# ---------------------------------------------------------------------------
# Test: real mode (dry_run=False) — crm_upsert calls writeback.crm.upsert_account
# ---------------------------------------------------------------------------

class TestRealMode:
    """In real mode, writeback.crm.upsert_account is called and its return is stored."""

    def test_crm_upsert_calls_real_crm_adapter(self):
        sentinel = "attio rec_abc (ICP 8) + evidence note"
        rr = _make_run_result(tier="Tier 1", needs_human_research=False)

        with patch("router.run_agent", side_effect=_fake_run_agent_factory(["crm_upsert"])), \
             patch("writeback.crm.upsert_account", return_value=sentinel) as mock_upsert:
            router.run_router(rr, dry_run=False)

        mock_upsert.assert_called_once_with(rr.score)
        assert rr.crm_status == sentinel

    def test_crm_status_is_set_to_adapter_return_value(self):
        expected = "hubspot company hs_999 (icp_score=8) + evidence note"
        rr = _make_run_result()

        with patch("router.run_agent", side_effect=_fake_run_agent_factory(["crm_upsert"])), \
             patch("writeback.crm.upsert_account", return_value=expected):
            router.run_router(rr, dry_run=False)

        assert rr.crm_status == expected


# ---------------------------------------------------------------------------
# Test: real-mode safety guard fires BEFORE lazy adapter import
# ---------------------------------------------------------------------------

class TestRealModeSafetyGuardBeforeImport:
    """Guard short-circuits before the lazy import, so missing modules don't raise."""

    @pytest.mark.parametrize("tier,needs_human_research", [
        ("Tier 2", False),
        ("Tier 1", True),
    ])
    def test_guard_fires_before_lazy_import(self, tier, needs_human_research):
        """Prove the safety guard returns the refusal string BEFORE attempting to import
        writeback.slack or writeback.outreach, even in real mode (dry_run=False).

        writeback/slack.py and writeback/outreach.py do not exist yet.  If the guard
        did NOT fire first, the lazy import would raise ImportError/ModuleNotFoundError.
        The fact that the call returns the refusal string — and neither module appears
        in sys.modules — proves the guard short-circuits before the import.
        """
        # Clear any cached entries for the non-existent modules
        sys.modules.pop("writeback.slack", None)
        sys.modules.pop("writeback.outreach", None)

        rr = _make_run_result(tier=tier, needs_human_research=needs_human_research)
        captured_returns = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            captured_returns["slack"] = impls["slack_alert"]()
            captured_returns["outreach"] = impls["outreach_queue"]()

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=False)

        # Both tools must return the refusal string
        assert captured_returns["slack"] == "refused: not a confident Tier 1 (safety guard)"
        assert captured_returns["outreach"] == "refused: not a confident Tier 1 (safety guard)"

        # Status fields must remain untouched (guard returned before setting them)
        assert rr.slack_status == "skipped"
        assert rr.outreach_status == "skipped"

        # The non-existent modules must NOT have been imported
        assert "writeback.slack" not in sys.modules, (
            "writeback.slack was imported — guard did not fire before the lazy import"
        )
        assert "writeback.outreach" not in sys.modules, (
            "writeback.outreach was imported — guard did not fire before the lazy import"
        )


# ---------------------------------------------------------------------------
# Test: slack/outreach lazy imports (module-not-found should not crash at import)
# ---------------------------------------------------------------------------

class TestLazyImports:
    """router.py must import without error even when writeback.slack/outreach don't exist."""

    def test_router_module_importable(self):
        """Importing router does not raise even if slack/outreach are absent."""
        import importlib
        # router is already imported; ensure no AttributeError on its public symbols
        assert hasattr(router, "ROUTER_SYSTEM")
        assert hasattr(router, "run_router")
        assert hasattr(router, "_tool_schema")

    def test_slack_real_mode_raises_import_error_without_crashing_router(self):
        """In real mode with slack missing, calling slack_alert raises ImportError inside tool.

        The fake run_agent here captures the ImportError rather than letting it
        propagate — consistent with how run_agent catches tool exceptions in prod.
        """
        rr = _make_run_result(tier="Tier 1", needs_human_research=False)

        # Ensure writeback.slack is absent from sys.modules
        sys.modules.pop("writeback.slack", None)
        sys.modules.pop("writeback.outreach", None)

        error_captured = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            try:
                impls["slack_alert"]()
            except (ImportError, ModuleNotFoundError) as e:
                error_captured["err"] = str(e)

        with patch("router.run_agent", side_effect=capturing_run_agent):
            # Should not crash at the router level
            router.run_router(rr, dry_run=False)

        # The ImportError should have been raised inside the tool, not propagated up
        assert "err" in error_captured, "Expected ImportError was not raised"
        assert "slack" in error_captured["err"]


# ---------------------------------------------------------------------------
# Test: finish tool and log parameter
# ---------------------------------------------------------------------------

class TestFinishAndLog:
    """finish() returns 'done'; log list is forwarded to run_agent."""

    def test_finish_returns_done(self):
        rr = _make_run_result()
        captured = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            captured["result"] = impls["finish"]()
            captured["final_tools"] = set(final_tools)

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=True)

        assert captured["result"] == "done"
        assert "finish" in captured["final_tools"]

    def test_log_forwarded_to_run_agent(self):
        rr = _make_run_result()
        agent_log = []
        log_received = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            log_received["log"] = log

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=True, log=agent_log)

        assert log_received["log"] is agent_log

    def test_max_turns_is_6(self):
        rr = _make_run_result()
        captured = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            captured["max_turns"] = max_turns

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=True)

        assert captured["max_turns"] == 6


# ---------------------------------------------------------------------------
# Test: user message sent to run_agent is valid JSON with expected keys
# ---------------------------------------------------------------------------

class TestUserMessage:
    """The user message passed to run_agent is a JSON object with the score fields."""

    def test_user_message_is_valid_json(self):
        import json
        rr = _make_run_result(company_name="TestCo", domain="test.co", score=7, tier="Tier 2")
        captured = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            captured["user"] = user

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=True)

        data = json.loads(captured["user"])
        assert data["company"] == "TestCo"
        assert data["domain"] == "test.co"
        assert data["score"] == 7
        assert data["tier"] == "Tier 2"

    def test_user_message_contains_routing_keys(self):
        import json
        rr = _make_run_result()
        captured = {}

        def capturing_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            captured["user"] = user

        with patch("router.run_agent", side_effect=capturing_run_agent):
            router.run_router(rr, dry_run=True)

        data = json.loads(captured["user"])
        for key in ("company", "domain", "score", "tier", "confidence",
                    "needs_human_research", "persona", "angle"):
            assert key in data, f"Missing key: {key}"
