"""Tests for writeback/outreach.py — dispatcher routing by OUTREACH_PROVIDER (async)."""
import logging
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from duvo import config
from duvo.writeback import outreach
from tests.conftest import make_score


@contextmanager
def _capture_duvo_logs(level: int = logging.WARNING):
    """Context manager that captures log records from the non-propagating 'duvo' logger.

    pytest's caplog cannot intercept records from a logger whose propagate=False.
    This helper attaches a MemoryHandler directly to the 'duvo' logger and yields
    the collected records list so tests can assert on them.
    """
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _ListHandler(level)
    duvo_logger = logging.getLogger("duvo")
    duvo_logger.addHandler(handler)
    try:
        yield records
    finally:
        duvo_logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# queue_lead — brevo branch
# ---------------------------------------------------------------------------

class TestQueueLeadBrevo:
    async def test_calls_brevo_queue_lead_when_provider_is_brevo(self, monkeypatch):
        monkeypatch.setattr(config, "OUTREACH_PROVIDER", "brevo")
        stub_result = "contact queued in Brevo review list 42 (not sent — rep reviews & sends)"

        with patch("duvo.writeback.brevo.queue_lead", AsyncMock(return_value=stub_result)) as mock_brevo:
            result = await outreach.queue_lead(make_score(), "test@example.com")

        mock_brevo.assert_called_once()
        assert result == stub_result

    async def test_brevo_receives_score_and_email(self, monkeypatch):
        monkeypatch.setattr(config, "OUTREACH_PROVIDER", "brevo")
        score = make_score(company_name="TestCo")

        with patch("duvo.writeback.brevo.queue_lead", AsyncMock(return_value="ok")) as mock_brevo:
            await outreach.queue_lead(score, "rep@test.com")

        args = mock_brevo.call_args[0]
        assert args[0] is score
        assert args[1] == "rep@test.com"


# ---------------------------------------------------------------------------
# queue_lead — lemlist branch
# ---------------------------------------------------------------------------

class TestQueueLeadLemlist:
    async def test_calls_lemlist_queue_lead_when_provider_is_lemlist(self, monkeypatch):
        monkeypatch.setattr(config, "OUTREACH_PROVIDER", "lemlist")
        stub_result = "lead queued in paused lemlist campaign cam_abc (awaiting rep approval)"

        with patch("duvo.writeback.lemlist.queue_lead", AsyncMock(return_value=stub_result)) as mock_lemlist:
            result = await outreach.queue_lead(make_score(), "test@example.com")

        mock_lemlist.assert_called_once()
        assert result == stub_result

    async def test_lemlist_receives_score_and_email(self, monkeypatch):
        monkeypatch.setattr(config, "OUTREACH_PROVIDER", "lemlist")
        score = make_score(company_name="LemCo")

        with patch("duvo.writeback.lemlist.queue_lead", AsyncMock(return_value="ok")) as mock_lemlist:
            await outreach.queue_lead(score, "lem@test.com")

        args = mock_lemlist.call_args[0]
        assert args[0] is score
        assert args[1] == "lem@test.com"


# ---------------------------------------------------------------------------
# queue_lead — unknown provider falls back to brevo with a warning
# ---------------------------------------------------------------------------

class TestQueueLeadUnknownProvider:
    async def test_unknown_provider_falls_back_to_brevo(self, monkeypatch):
        monkeypatch.setattr(config, "OUTREACH_PROVIDER", "mailchimp")
        stub_result = "contact queued in Brevo review list 42 (not sent — rep reviews & sends)"

        with _capture_duvo_logs(logging.WARNING) as records:
            with patch("duvo.writeback.brevo.queue_lead", AsyncMock(return_value=stub_result)) as mock_brevo:
                result = await outreach.queue_lead(make_score(), "test@example.com")

        mock_brevo.assert_called_once()
        assert result == stub_result
        assert any(
            "unknown OUTREACH_PROVIDER" in r.getMessage() and "mailchimp" in r.getMessage()
            for r in records
        ), "Expected a warning about unknown OUTREACH_PROVIDER=mailchimp"

    async def test_brevo_provider_does_not_warn(self, monkeypatch):
        monkeypatch.setattr(config, "OUTREACH_PROVIDER", "brevo")
        stub_result = "contact queued in Brevo review list 42 (not sent — rep reviews & sends)"

        with _capture_duvo_logs(logging.WARNING) as records:
            with patch("duvo.writeback.brevo.queue_lead", AsyncMock(return_value=stub_result)):
                await outreach.queue_lead(make_score(), "test@example.com")

        unknown_warns = [
            r for r in records
            if "unknown OUTREACH_PROVIDER" in r.getMessage()
        ]
        assert not unknown_warns, "No unknown-provider warning should fire for provider='brevo'"


# ---------------------------------------------------------------------------
# Lazy imports — no keys needed to import the dispatcher itself
# ---------------------------------------------------------------------------

class TestLazyImports:
    def test_outreach_module_imports_without_env_keys(self):
        """The dispatcher must be importable even when outreach keys are absent."""
        # If we get here, the import at the top of this file succeeded without keys.
        assert hasattr(outreach, "queue_lead")
