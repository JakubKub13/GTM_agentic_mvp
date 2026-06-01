"""Tests for writeback/crm.py — the dispatcher."""
from unittest.mock import MagicMock, patch

import pytest

import config
from models import ICPScore, OutreachDraft
from writeback import crm


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

def test_dispatcher_routes_to_attio(score, monkeypatch):
    """When CRM_PROVIDER == 'attio', the attio adapter's upsert_account is called."""
    monkeypatch.setattr(config, "CRM_PROVIDER", "attio")

    sentinel = "attio company rec_123 (ICP 8) + evidence note"
    mock_attio_upsert = MagicMock(return_value=sentinel)

    with patch("writeback.attio.upsert_account", mock_attio_upsert):
        result = crm.upsert_account(score)

    mock_attio_upsert.assert_called_once_with(score)
    assert result == sentinel


def test_dispatcher_routes_to_hubspot(score, monkeypatch):
    """When CRM_PROVIDER == 'hubspot', the hubspot adapter's upsert_account is called."""
    monkeypatch.setattr(config, "CRM_PROVIDER", "hubspot")

    sentinel = "company hs_456 (icp_score=8) + evidence note"
    mock_hubspot_upsert = MagicMock(return_value=sentinel)

    with patch("writeback.hubspot.upsert_account", mock_hubspot_upsert):
        result = crm.upsert_account(score)

    mock_hubspot_upsert.assert_called_once_with(score)
    assert result == sentinel


def test_dispatcher_returns_provider_result(score, monkeypatch):
    """upsert_account propagates the adapter's return value unchanged."""
    monkeypatch.setattr(config, "CRM_PROVIDER", "attio")

    expected = "attio company abc (ICP 8) + evidence note"
    with patch("writeback.attio.upsert_account", return_value=expected):
        assert crm.upsert_account(score) == expected


def test_importing_crm_without_hubspot_token(monkeypatch):
    """Importing writeback.crm should succeed even when HUBSPOT_TOKEN is absent."""
    monkeypatch.setattr(config, "HUBSPOT_TOKEN", "")
    # Should not raise at import time
    from writeback import crm  # noqa: F401
    assert crm is not None
