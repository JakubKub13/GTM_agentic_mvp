"""Tests for writeback/slack.py — Tier-1 alert via incoming webhook."""
from unittest.mock import patch

import pytest

import config
from tests.conftest import _fake_response, make_score
from writeback import slack


# ---------------------------------------------------------------------------
# alert_tier1 — happy path
# ---------------------------------------------------------------------------

class TestAlertTier1:
    def test_posts_to_webhook_url(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake/webhook")
        ok_resp = _fake_response(200)

        with patch("writeback.slack.requests.post", return_value=ok_resp) as mock_post:
            slack.alert_tier1(make_score())

        call_url = mock_post.call_args[0][0]
        assert call_url == "https://hooks.slack.com/fake/webhook"

    def test_posts_json_with_blocks_and_text_fallback(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake/webhook")
        ok_resp = _fake_response(200)

        with patch("writeback.slack.requests.post", return_value=ok_resp) as mock_post:
            slack.alert_tier1(make_score())

        kwargs = mock_post.call_args[1]
        body = kwargs["json"]
        assert "blocks" in body
        assert "text" in body
        assert "Acme Corp" in body["text"]

    def test_header_block_contains_company_name_and_score(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake/webhook")
        ok_resp = _fake_response(200)

        with patch("writeback.slack.requests.post", return_value=ok_resp) as mock_post:
            slack.alert_tier1(make_score())

        body = mock_post.call_args[1]["json"]
        header_block = body["blocks"][0]
        assert header_block["type"] == "header"
        header_text = header_block["text"]["text"]
        assert "Acme Corp" in header_text
        assert "8" in header_text  # score

    def test_returns_confirmation_string(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake/webhook")
        ok_resp = _fake_response(200)

        with patch("writeback.slack.requests.post", return_value=ok_resp):
            result = slack.alert_tier1(make_score())

        assert result == "alert posted to #sales"

    def test_raises_on_non_2xx_response(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/fake/webhook")
        err_resp = _fake_response(400, raise_on_raise=True)

        with patch("writeback.slack.requests.post", return_value=err_resp):
            with pytest.raises(Exception, match="HTTP 400"):
                slack.alert_tier1(make_score())


# ---------------------------------------------------------------------------
# require guard — SLACK_WEBHOOK_URL must be present
# ---------------------------------------------------------------------------

class TestRequireWebhookUrl:
    def test_raises_when_webhook_url_empty(self, monkeypatch):
        monkeypatch.setattr(config, "SLACK_WEBHOOK_URL", "")
        ok_resp = _fake_response(200)

        with patch("writeback.slack.requests.post", return_value=ok_resp):
            with pytest.raises(RuntimeError, match="SLACK_WEBHOOK_URL"):
                slack.alert_tier1(make_score())
