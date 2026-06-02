"""Tests for writeback/hubspot.py — HubSpot CRM adapter (async httpx)."""
from unittest.mock import AsyncMock, patch

import pytest

from duvo import config
from duvo.models import ICPScore
from duvo.writeback import hubspot
from tests.conftest import _fake_response, make_fake_async_client
from tests.conftest import make_score as _make_score

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_score(needs_human_research: bool = False) -> ICPScore:
    """HubSpot-specific defaults wrapping the shared make_score factory."""
    return _make_score(
        score=7,
        tier="Tier 2",
        confidence="medium",
        why_fit=["fast growth", "matching ICP"],
        why_not=["unknown budget"],
        recommended_persona="Head of Finance",
        recommended_angle="efficiency gains",
        reasoning="Medium-fit company with some signals.",
        needs_human_research=needs_human_research,
        outreach_persona="Head of Finance",
        outreach_subject="Cut costs at Acme",
        outreach_first_line="Saw your recent expansion.",
        outreach_body="Full body content.",
    )


# ---------------------------------------------------------------------------
# _note_body
# ---------------------------------------------------------------------------

class TestNoteBody:
    def test_uses_br_separator(self):
        body = hubspot._note_body(make_score())
        assert "<br>" in body

    def test_contains_ai_suggested(self):
        body = hubspot._note_body(make_score())
        assert "AI-SUGGESTED" in body

    def test_contains_score_tier_confidence(self):
        body = hubspot._note_body(make_score())
        assert "7/10" in body
        assert "Tier 2" in body
        assert "medium" in body

    def test_contains_why_fit_and_why_not(self):
        body = hubspot._note_body(make_score())
        assert "fast growth" in body
        assert "unknown budget" in body

    def test_contains_reasoning(self):
        body = hubspot._note_body(make_score())
        assert "Medium-fit company with some signals." in body

    def test_contains_opener(self):
        body = hubspot._note_body(make_score())
        assert "Saw your recent expansion." in body

    def test_flagged_line_present_when_needs_human_research(self):
        body = hubspot._note_body(make_score(needs_human_research=True))
        assert ">> FLAGGED" in body
        assert "human research needed" in body

    def test_flagged_line_absent_when_not_needed(self):
        body = hubspot._note_body(make_score(needs_human_research=False))
        assert ">> FLAGGED" not in body


# ---------------------------------------------------------------------------
# ensure_icp_property
# ---------------------------------------------------------------------------

class TestEnsureIcpProperty:
    async def test_does_not_post_when_property_exists(self, monkeypatch):
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "tok")
        get_resp = _fake_response(200)
        client = make_fake_async_client(
            get=AsyncMock(return_value=get_resp),
            post=AsyncMock(return_value=_fake_response(200)),
        )

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await hubspot.ensure_icp_property()

        client.get.assert_called_once()
        client.post.assert_not_called()

    async def test_posts_property_when_not_found(self, monkeypatch):
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "tok")
        get_resp = _fake_response(404)
        post_resp = _fake_response(201)
        client = make_fake_async_client(
            get=AsyncMock(return_value=get_resp),
            post=AsyncMock(return_value=post_resp),
        )

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await hubspot.ensure_icp_property()

        client.post.assert_called_once()
        call_json = client.post.call_args[1]["json"]
        assert call_json["name"] == "icp_score"


# ---------------------------------------------------------------------------
# _upsert_company
# ---------------------------------------------------------------------------

class TestUpsertCompany:
    async def test_patches_existing_company_when_found(self, monkeypatch):
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "tok")
        existing_id = "hs_existing_123"
        search_resp = _fake_response(200, {"results": [{"id": existing_id}]})
        patch_resp = _fake_response(200, {"id": existing_id})

        client = make_fake_async_client(
            post=AsyncMock(return_value=search_resp),
            patch=AsyncMock(return_value=patch_resp),
        )

        with patch("duvo.infra.http_client.get_client", return_value=client):
            result = await hubspot._upsert_company(make_score())

        assert result == existing_id
        client.patch.assert_called_once()
        patch_url = client.patch.call_args[0][0]
        assert existing_id in patch_url

    async def test_creates_new_company_when_not_found(self, monkeypatch):
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "tok")
        new_id = "hs_new_456"
        search_resp = _fake_response(200, {"results": []})
        create_resp = _fake_response(201, {"id": new_id})

        client = make_fake_async_client(
            post=AsyncMock(side_effect=[search_resp, create_resp]),
            patch=AsyncMock(return_value=_fake_response(200)),
        )

        with patch("duvo.infra.http_client.get_client", return_value=client):
            result = await hubspot._upsert_company(make_score())

        assert result == new_id
        client.patch.assert_not_called()
        assert client.post.call_count == 2

    async def test_patch_payload_contains_icp_score(self, monkeypatch):
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "tok")
        existing_id = "hs_existing_789"
        search_resp = _fake_response(200, {"results": [{"id": existing_id}]})
        patch_resp = _fake_response(200, {"id": existing_id})

        client = make_fake_async_client(
            post=AsyncMock(return_value=search_resp),
            patch=AsyncMock(return_value=patch_resp),
        )

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await hubspot._upsert_company(make_score())

        props = client.patch.call_args[1]["json"]["properties"]
        assert props["icp_score"] == 7

    async def test_search_post_raises_on_non_2xx(self, monkeypatch):
        """Non-2xx on the search POST must propagate (raise_for_status is called)."""
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "tok")
        error_resp = _fake_response(400, raise_on_raise=True)

        client = make_fake_async_client(post=AsyncMock(return_value=error_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            with pytest.raises(Exception, match="HTTP 400"):
                await hubspot._upsert_company(make_score())

    async def test_create_post_raises_on_non_2xx(self, monkeypatch):
        """Non-2xx on the company-creation POST must propagate (raise_for_status is called)."""
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "tok")
        search_resp = _fake_response(200, {"results": []})
        error_resp = _fake_response(500, raise_on_raise=True)

        client = make_fake_async_client(
            post=AsyncMock(side_effect=[search_resp, error_resp]),
        )

        with patch("duvo.infra.http_client.get_client", return_value=client):
            with pytest.raises(Exception, match="HTTP 500"):
                await hubspot._upsert_company(make_score())


# ---------------------------------------------------------------------------
# _create_note — raise_for_status
# ---------------------------------------------------------------------------

class TestCreateNote:
    async def test_note_post_raises_on_non_2xx(self, monkeypatch):
        """Non-2xx on the notes POST must propagate (raise_for_status is called)."""
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "tok")
        error_resp = _fake_response(503, raise_on_raise=True)

        client = make_fake_async_client(post=AsyncMock(return_value=error_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            with pytest.raises(Exception, match="HTTP 503"):
                await hubspot._create_note(make_score(), "company_123")


# ---------------------------------------------------------------------------
# upsert_account (full chain)
# ---------------------------------------------------------------------------

class TestUpsertAccount:
    async def test_returns_string_with_company_id_and_score(self, monkeypatch):
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "tok")
        company_id = "hs_chain_001"

        get_resp = _fake_response(200)           # ensure_icp_property GET → exists
        search_resp = _fake_response(200, {"results": []})
        create_resp = _fake_response(201, {"id": company_id})
        note_resp = _fake_response(201, {"id": "note_001"})

        client = make_fake_async_client(
            get=AsyncMock(return_value=get_resp),
            post=AsyncMock(side_effect=[search_resp, create_resp, note_resp]),
        )

        with patch("duvo.infra.http_client.get_client", return_value=client):
            result = await hubspot.upsert_account(make_score())

        assert company_id in result
        assert "7" in result  # icp_score

    async def test_result_mentions_evidence_note(self, monkeypatch):
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "tok")
        company_id = "hs_chain_002"

        get_resp = _fake_response(200)
        search_resp = _fake_response(200, {"results": []})
        create_resp = _fake_response(201, {"id": company_id})
        note_resp = _fake_response(201)

        client = make_fake_async_client(
            get=AsyncMock(return_value=get_resp),
            post=AsyncMock(side_effect=[search_resp, create_resp, note_resp]),
        )

        with patch("duvo.infra.http_client.get_client", return_value=client):
            result = await hubspot.upsert_account(make_score())

        assert "evidence note" in result

    def test_raises_when_hubspot_token_missing(self, monkeypatch):
        monkeypatch.setattr(config, "HUBSPOT_TOKEN", "")
        with pytest.raises(RuntimeError, match="HUBSPOT_TOKEN"):
            hubspot._headers()
