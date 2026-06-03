"""Tests for writeback/slack.py — Tier-1 alert via incoming webhook (async httpx)."""

from unittest.mock import AsyncMock, patch

import pytest

from duvo import config
from duvo.writeback import slack
from tests.conftest import _fake_response, make_fake_async_client, make_score

# ---------------------------------------------------------------------------
# alert_tier1 — happy path
# ---------------------------------------------------------------------------


class TestAlertTier1:
    async def test_posts_to_webhook_url(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake/webhook")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await slack.alert_tier1(make_score())

        call_url = client.post.call_args[0][0]
        assert call_url == "https://hooks.slack.com/fake/webhook"

    async def test_posts_json_with_blocks_and_text_fallback(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake/webhook")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await slack.alert_tier1(make_score())

        kwargs = client.post.call_args[1]
        body = kwargs["json"]
        assert "blocks" in body
        assert "text" in body
        assert "Acme Corp" in body["text"]

    async def test_header_block_contains_company_name_and_score(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake/webhook")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            await slack.alert_tier1(make_score())

        body = client.post.call_args[1]["json"]
        header_block = body["blocks"][0]
        assert header_block["type"] == "header"
        header_text = header_block["text"]["text"]
        assert "Acme Corp" in header_text
        assert "8" in header_text  # score

    async def test_returns_confirmation_string(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake/webhook")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            result = await slack.alert_tier1(make_score())

        assert result == "alert posted to #sales"

    async def test_raises_on_non_2xx_response(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake/webhook")
        err_resp = _fake_response(400, raise_on_raise=True)
        client = make_fake_async_client(post=AsyncMock(return_value=err_resp))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            with pytest.raises(Exception, match="HTTP 400"):
                await slack.alert_tier1(make_score())


# ---------------------------------------------------------------------------
# require guard — SLACK_WEBHOOK_URL must be present
# ---------------------------------------------------------------------------


class TestRequireWebhookUrl:
    async def test_raises_when_webhook_url_empty(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "")
        client = make_fake_async_client(post=AsyncMock(return_value=_fake_response(200)))

        with patch("duvo.infra.http_client.get_client", return_value=client):
            with pytest.raises(RuntimeError, match="SLACK_WEBHOOK_URL"):
                await slack.alert_tier1(make_score())
