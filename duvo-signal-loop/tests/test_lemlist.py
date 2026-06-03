"""Tests for writeback/lemlist.py — lemlist adapter (paused-campaign draft) (async httpx)."""

from unittest.mock import AsyncMock, patch

import pytest

from duvo import config
from duvo.writeback import lemlist
from tests.conftest import _fake_response, make_fake_async_client, make_score

# ---------------------------------------------------------------------------
# queue_lead — happy path
# ---------------------------------------------------------------------------


class TestQueueLead:
    async def test_posts_to_leads_url(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await lemlist.queue_lead(make_score(), "lead@example.com")

        call_url = client.post.call_args[0][0]
        assert "/campaigns/cam_abc123/leads" in call_url

    async def test_posts_with_basic_auth(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key-789")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await lemlist.queue_lead(make_score(), "lead@example.com")

        kwargs = client.post.call_args[1]
        assert kwargs["auth"] == ("", "lem-key-789")

    async def test_posts_with_deduplicate_param(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await lemlist.queue_lead(make_score(), "lead@example.com")

        kwargs = client.post.call_args[1]
        assert kwargs["params"] == {"deduplicate": "true"}

    async def test_payload_includes_required_fields(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)
        score = make_score()
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await lemlist.queue_lead(score, "lead@example.com")

        body = client.post.call_args[1]["json"]
        assert body["email"] == "lead@example.com"
        assert body["companyName"] == "Acme Corp"
        assert body["companyDomain"] == "acme.com"
        assert body["jobTitle"] == "VP of Ops"
        assert body["icpScore"] == "8"
        assert body["icebreaker"] == "Hi, noticed you're scaling quickly."

    async def test_returns_confirmation_string_with_campaign_id(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            result = await lemlist.queue_lead(make_score(), "lead@example.com")

        assert "cam_abc123" in result
        assert "paused" in result or "awaiting" in result

    async def test_raises_on_non_2xx_response(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        err_resp = _fake_response(422, raise_on_raise=True)
        client = make_fake_async_client(post=AsyncMock(return_value=err_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            with pytest.raises(Exception, match="HTTP 422"):
                await lemlist.queue_lead(make_score(), "lead@example.com")

    async def test_never_calls_send_endpoint(self, monkeypatch):
        """Confirm only the leads endpoint is called — no send endpoint."""
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await lemlist.queue_lead(make_score(), "lead@example.com")

        for c in client.post.call_args_list:
            url = c[0][0]
            assert "send" not in url.lower(), f"send endpoint must never be called, got: {url}"


# ---------------------------------------------------------------------------
# require guard
# ---------------------------------------------------------------------------


class TestRequireKeys:
    async def test_raises_when_lemlist_api_key_missing(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        client = make_fake_async_client(post=AsyncMock(return_value=_fake_response(200)))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            with pytest.raises(RuntimeError, match="LEMLIST_API_KEY"):
                await lemlist.queue_lead(make_score(), "lead@example.com")

    async def test_raises_when_campaign_id_missing(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "")
        client = make_fake_async_client(post=AsyncMock(return_value=_fake_response(200)))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            with pytest.raises(RuntimeError, match="LEMLIST_CAMPAIGN_ID"):
                await lemlist.queue_lead(make_score(), "lead@example.com")
