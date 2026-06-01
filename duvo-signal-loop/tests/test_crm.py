"""Tests for writeback/crm.py — the dispatcher (async)."""
import logging
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

import config
from models import ICPScore, OutreachDraft
from writeback import crm


@contextmanager
def _capture_duvo_logs(level: int = logging.WARNING):
    """Capture log records from the non-propagating 'duvo' logger."""
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
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def score() -> ICPScore:
    return ICPScore(
        company_name="Acme Corp",
        domain="acme.com",
        score=8,
        tier="Tier 1",
        confidence="high",
        why_fit=["scales fast", "right persona"],
        why_not=["competitive market"],
        recommended_persona="VP of Ops",
        recommended_angle="cost reduction",
        reasoning="Strong fit based on hiring signals.",
        needs_human_research=False,
        outreach=OutreachDraft(
            persona="VP of Ops",
            subject="Streamline ops at Acme",
            first_line="Hi, noticed you're scaling quickly.",
            body="Full body here.",
        ),
    )


# ---------------------------------------------------------------------------
# Dispatcher routes to Attio by default
# ---------------------------------------------------------------------------

async def test_dispatcher_routes_to_attio(score, monkeypatch):
    """When CRM_PROVIDER == 'attio', the attio adapter's upsert_account is called."""
    monkeypatch.setattr(config, "CRM_PROVIDER", "attio")

    sentinel = "attio company rec_123 (ICP 8) + evidence note"
    mock_attio_upsert = AsyncMock(return_value=sentinel)

    with patch("writeback.attio.upsert_account", mock_attio_upsert):
        result = await crm.upsert_account(score)

    mock_attio_upsert.assert_called_once_with(score)
    assert result == sentinel


async def test_dispatcher_routes_to_hubspot(score, monkeypatch):
    """When CRM_PROVIDER == 'hubspot', the hubspot adapter's upsert_account is called."""
    monkeypatch.setattr(config, "CRM_PROVIDER", "hubspot")

    sentinel = "company hs_456 (icp_score=8) + evidence note"
    mock_hubspot_upsert = AsyncMock(return_value=sentinel)

    with patch("writeback.hubspot.upsert_account", mock_hubspot_upsert):
        result = await crm.upsert_account(score)

    mock_hubspot_upsert.assert_called_once_with(score)
    assert result == sentinel


async def test_dispatcher_returns_provider_result(score, monkeypatch):
    """upsert_account propagates the adapter's return value unchanged."""
    monkeypatch.setattr(config, "CRM_PROVIDER", "attio")

    expected = "attio company abc (ICP 8) + evidence note"
    with patch("writeback.attio.upsert_account", AsyncMock(return_value=expected)):
        assert await crm.upsert_account(score) == expected


def test_importing_crm_without_hubspot_token(monkeypatch):
    """Importing writeback.crm should succeed even when HUBSPOT_TOKEN is absent."""
    monkeypatch.setattr(config, "HUBSPOT_TOKEN", "")
    # Should not raise at import time
    from writeback import crm  # noqa: F401
    assert crm is not None


# ---------------------------------------------------------------------------
# Dispatcher unknown provider — routes to attio with a warning
# ---------------------------------------------------------------------------

async def test_unknown_provider_routes_to_attio_and_warns(score, monkeypatch):
    """An unknown CRM_PROVIDER value routes to attio and emits a warning."""
    monkeypatch.setattr(config, "CRM_PROVIDER", "salesforce")

    sentinel = "attio company rec_unknown (ICP 8) + evidence note"
    mock_attio_upsert = AsyncMock(return_value=sentinel)

    with _capture_duvo_logs(logging.WARNING) as records:
        with patch("writeback.attio.upsert_account", mock_attio_upsert):
            result = await crm.upsert_account(score)

    mock_attio_upsert.assert_called_once_with(score)
    assert result == sentinel
    assert any(
        "unknown CRM_PROVIDER" in r.getMessage() and "salesforce" in r.getMessage()
        for r in records
    ), "Expected a warning about unknown CRM_PROVIDER='salesforce'"


async def test_attio_provider_does_not_warn(score, monkeypatch):
    """When CRM_PROVIDER == 'attio', no unknown-provider warning is emitted."""
    monkeypatch.setattr(config, "CRM_PROVIDER", "attio")

    with _capture_duvo_logs(logging.WARNING) as records:
        with patch("writeback.attio.upsert_account", AsyncMock(return_value="ok")):
            await crm.upsert_account(score)

    unknown_warns = [r for r in records if "unknown CRM_PROVIDER" in r.getMessage()]
    assert not unknown_warns, "No unknown-provider warning should fire for provider='attio'"
