"""Tests for writeback/lemlist.py — lemlist adapter (paused-campaign draft)."""
from unittest.mock import patch

import pytest

import config
from tests.conftest import _fake_response, make_score
from writeback import lemlist


# ---------------------------------------------------------------------------
# queue_lead — happy path
# ---------------------------------------------------------------------------

class TestQueueLead:
    def test_posts_to_leads_url(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)

        with patch("writeback.lemlist.requests.post", return_value=ok_resp) as mock_post:
            lemlist.queue_lead(make_score(), "lead@example.com")

        call_url = mock_post.call_args[0][0]
        assert "/campaigns/cam_abc123/leads" in call_url

    def test_posts_with_basic_auth(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key-789")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)

        with patch("writeback.lemlist.requests.post", return_value=ok_resp) as mock_post:
            lemlist.queue_lead(make_score(), "lead@example.com")

        kwargs = mock_post.call_args[1]
        assert kwargs["auth"] == ("", "lem-key-789")

    def test_posts_with_deduplicate_param(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)

        with patch("writeback.lemlist.requests.post", return_value=ok_resp) as mock_post:
            lemlist.queue_lead(make_score(), "lead@example.com")

        kwargs = mock_post.call_args[1]
        assert kwargs["params"] == {"deduplicate": "true"}

    def test_payload_includes_required_fields(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)
        score = make_score()

        with patch("writeback.lemlist.requests.post", return_value=ok_resp) as mock_post:
            lemlist.queue_lead(score, "lead@example.com")

        body = mock_post.call_args[1]["json"]
        assert body["email"] == "lead@example.com"
        assert body["companyName"] == "Acme Corp"
        assert body["companyDomain"] == "acme.com"
        assert body["jobTitle"] == "VP of Ops"
        assert body["icpScore"] == "8"
        assert body["icebreaker"] == "Hi, noticed you're scaling quickly."

    def test_returns_confirmation_string_with_campaign_id(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)

        with patch("writeback.lemlist.requests.post", return_value=ok_resp):
            result = lemlist.queue_lead(make_score(), "lead@example.com")

        assert "cam_abc123" in result
        assert "paused" in result or "awaiting" in result

    def test_raises_on_non_2xx_response(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        err_resp = _fake_response(422, raise_on_raise=True)

        with patch("writeback.lemlist.requests.post", return_value=err_resp):
            with pytest.raises(Exception, match="HTTP 422"):
                lemlist.queue_lead(make_score(), "lead@example.com")

    def test_never_calls_send_endpoint(self, monkeypatch):
        """Confirm only the leads endpoint is called — no send endpoint."""
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)

        with patch("writeback.lemlist.requests.post", return_value=ok_resp) as mock_post:
            lemlist.queue_lead(make_score(), "lead@example.com")

        for c in mock_post.call_args_list:
            url = c[0][0]
            assert "send" not in url.lower(), f"send endpoint must never be called, got: {url}"


# ---------------------------------------------------------------------------
# require guard
# ---------------------------------------------------------------------------

class TestRequireKeys:
    def test_raises_when_lemlist_api_key_missing(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "cam_abc123")
        ok_resp = _fake_response(200)

        with patch("writeback.lemlist.requests.post", return_value=ok_resp):
            with pytest.raises(RuntimeError, match="LEMLIST_API_KEY"):
                lemlist.queue_lead(make_score(), "lead@example.com")

    def test_raises_when_campaign_id_missing(self, monkeypatch):
        monkeypatch.setattr(config, "LEMLIST_API_KEY", "lem-key")
        monkeypatch.setattr(config, "LEMLIST_CAMPAIGN_ID", "")
        ok_resp = _fake_response(200)

        with patch("writeback.lemlist.requests.post", return_value=ok_resp):
            with pytest.raises(RuntimeError, match="LEMLIST_CAMPAIGN_ID"):
                lemlist.queue_lead(make_score(), "lead@example.com")
