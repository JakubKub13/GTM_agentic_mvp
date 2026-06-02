"""Tests for writeback/brevo.py — Brevo contact-list write-back (never sends) (async httpx)."""
from unittest.mock import AsyncMock, patch

import pytest

from duvo import config
from duvo.writeback import brevo
from tests.conftest import _fake_response, make_fake_async_client, make_score

# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------

class TestHeaders:
    def test_contains_api_key(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "brev-test-key")
        headers = brevo._headers()
        assert headers["api-key"] == "brev-test-key"
        assert headers["Content-Type"] == "application/json"
        assert headers["accept"] == "application/json"

    def test_raises_when_api_key_missing(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "")
        with pytest.raises(RuntimeError, match="BREVO_API_KEY"):
            brevo._headers()


# ---------------------------------------------------------------------------
# queue_lead — full payload succeeds
# ---------------------------------------------------------------------------

class TestQueueLeadFullPayload:
    async def test_posts_to_contacts_endpoint(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "key")
        monkeypatch.setattr(config, "BREVO_LIST_ID", "42")
        ok_resp = _fake_response(201)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await brevo.queue_lead(make_score(), "lead@example.com")

        call_url = client.post.call_args[0][0]
        assert call_url.endswith("/contacts")

    async def test_full_payload_includes_attributes(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "key")
        monkeypatch.setattr(config, "BREVO_LIST_ID", "42")
        ok_resp = _fake_response(201)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await brevo.queue_lead(make_score(), "lead@example.com")

        body = client.post.call_args[1]["json"]
        assert "attributes" in body
        assert body["attributes"]["COMPANY"] == "Acme Corp"
        assert "ICEBREAKER" in body["attributes"]
        assert body["attributes"]["ICEBREAKER"] == "Hi, noticed you're scaling quickly."

    async def test_full_payload_includes_list_id_as_int(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "key")
        monkeypatch.setattr(config, "BREVO_LIST_ID", "42")
        ok_resp = _fake_response(201)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await brevo.queue_lead(make_score(), "lead@example.com")

        body = client.post.call_args[1]["json"]
        assert body["listIds"] == [42]
        assert isinstance(body["listIds"][0], int)

    async def test_returns_confirmation_string_with_list_id(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "key")
        monkeypatch.setattr(config, "BREVO_LIST_ID", "42")
        ok_resp = _fake_response(201)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            result = await brevo.queue_lead(make_score(), "lead@example.com")

        assert "42" in result
        assert "Brevo" in result
        assert "not sent" in result or "rep" in result

    async def test_never_calls_send_endpoint(self, monkeypatch):
        """Confirm only /contacts is called — no send endpoint."""
        monkeypatch.setattr(config, "BREVO_API_KEY", "key")
        monkeypatch.setattr(config, "BREVO_LIST_ID", "42")
        ok_resp = _fake_response(201)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await brevo.queue_lead(make_score(), "lead@example.com")

        for c in client.post.call_args_list:
            url = c[0][0]
            assert "send" not in url.lower(), f"send endpoint must never be called, got: {url}"
            assert "email" not in url.lower() or url.endswith("/contacts"), (
                f"unexpected endpoint: {url}"
            )


# ---------------------------------------------------------------------------
# queue_lead — full payload fails, retry with minimal
# ---------------------------------------------------------------------------

class TestQueueLeadFallbackToMinimal:
    async def test_retries_with_minimal_payload_when_full_fails(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "key")
        monkeypatch.setattr(config, "BREVO_LIST_ID", "99")
        fail_resp = _fake_response(400)
        ok_resp = _fake_response(200)

        client = make_fake_async_client(post=AsyncMock(side_effect=[fail_resp, ok_resp]))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await brevo.queue_lead(make_score(), "lead@example.com")

        assert client.post.call_count == 2

    async def test_minimal_payload_has_no_attributes(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "key")
        monkeypatch.setattr(config, "BREVO_LIST_ID", "99")
        fail_resp = _fake_response(400)
        ok_resp = _fake_response(200)

        client = make_fake_async_client(post=AsyncMock(side_effect=[fail_resp, ok_resp]))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await brevo.queue_lead(make_score(), "lead@example.com")

        minimal_body = client.post.call_args_list[1][1]["json"]
        assert "attributes" not in minimal_body
        assert minimal_body["listIds"] == [99]
        assert minimal_body["email"] == "lead@example.com"

    async def test_returns_confirmation_string_when_fallback_succeeds(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "key")
        monkeypatch.setattr(config, "BREVO_LIST_ID", "99")
        fail_resp = _fake_response(400)
        ok_resp = _fake_response(200)

        client = make_fake_async_client(post=AsyncMock(side_effect=[fail_resp, ok_resp]))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            result = await brevo.queue_lead(make_score(), "lead@example.com")

        assert "99" in result
        assert "Brevo" in result


# ---------------------------------------------------------------------------
# queue_lead — both payloads fail → raise_for_status
# ---------------------------------------------------------------------------

class TestQueueLeadBothFail:
    async def test_raises_when_both_attempts_fail(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "key")
        monkeypatch.setattr(config, "BREVO_LIST_ID", "42")
        err_resp = _fake_response(500, raise_on_raise=True)

        client = make_fake_async_client(post=AsyncMock(return_value=err_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            with pytest.raises(Exception, match="HTTP 500"):
                await brevo.queue_lead(make_score(), "lead@example.com")


# ---------------------------------------------------------------------------
# BREVO_LIST_ID cast to int
# ---------------------------------------------------------------------------

class TestListIdCastToInt:
    async def test_list_id_cast_to_int(self, monkeypatch):
        monkeypatch.setattr(config, "BREVO_API_KEY", "key")
        monkeypatch.setattr(config, "BREVO_LIST_ID", "7")  # string in env
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await brevo.queue_lead(make_score(), "lead@example.com")

        body = client.post.call_args[1]["json"]
        assert body["listIds"] == [7]
        assert isinstance(body["listIds"][0], int)
