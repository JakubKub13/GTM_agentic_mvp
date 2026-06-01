"""Tests for writeback/attio.py — Attio CRM adapter."""
from unittest.mock import MagicMock, call, patch

import pytest

import config
from models import ICPScore, OutreachDraft
from writeback import attio
from tests.conftest import _fake_response, make_score


# ---------------------------------------------------------------------------
# _note_body
# ---------------------------------------------------------------------------

class TestNoteBody:
    def test_contains_ai_suggested(self):
        body = attio._note_body(make_score())
        assert "AI-SUGGESTED" in body

    def test_contains_score_tier_confidence(self):
        body = attio._note_body(make_score())
        assert "8/10" in body
        assert "Tier 1" in body
        assert "high" in body

    def test_contains_why_fit_joined(self):
        body = attio._note_body(make_score())
        assert "scales fast" in body
        assert "right persona" in body

    def test_contains_why_not_joined(self):
        body = attio._note_body(make_score())
        assert "competitive market" in body

    def test_contains_reasoning(self):
        body = attio._note_body(make_score())
        assert "Strong fit based on hiring signals." in body

    def test_contains_opener(self):
        body = attio._note_body(make_score())
        assert "Hi, noticed you're scaling quickly." in body

    def test_contains_persona_and_angle(self):
        body = attio._note_body(make_score())
        assert "VP of Ops" in body
        assert "cost reduction" in body

    def test_flagged_line_present_when_needs_human_research(self):
        body = attio._note_body(make_score(needs_human_research=True))
        assert ">> FLAGGED" in body
        assert "human research needed" in body

    def test_flagged_line_absent_when_not_needed(self):
        body = attio._note_body(make_score(needs_human_research=False))
        assert ">> FLAGGED" not in body


# ---------------------------------------------------------------------------
# _headers
# ---------------------------------------------------------------------------

class TestHeaders:
    def test_raises_when_api_key_missing(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "")
        # Need to reload so the module picks up the monkeypatched value at call time
        with pytest.raises(RuntimeError, match="ATTIO_API_KEY"):
            attio._headers()

    def test_contains_bearer_token(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "test-key-123")
        headers = attio._headers()
        assert headers["Authorization"] == "Bearer test-key-123"
        assert headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# _create_company
# ---------------------------------------------------------------------------

class TestCreateCompany:
    def test_posts_to_records_url_and_returns_record_id(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        record_id = "rec_abc123"
        ok_resp = _fake_response(200, {"data": {"id": {"record_id": record_id}}})

        with patch("writeback.attio.requests.post", return_value=ok_resp) as mock_post:
            result = attio._create_company(make_score())

        assert result == record_id
        call_url = mock_post.call_args[0][0]
        assert "/objects/companies/records" in call_url

    def test_retries_with_name_only_when_first_fails(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        record_id = "rec_fallback"

        fail_resp = _fake_response(422)
        ok_resp = _fake_response(200, {"data": {"id": {"record_id": record_id}}})

        with patch("writeback.attio.requests.post", side_effect=[fail_resp, ok_resp]) as mock_post:
            result = attio._create_company(make_score())

        assert result == record_id
        assert mock_post.call_count == 2
        # First call has "domains" in the payload
        first_values = mock_post.call_args_list[0][1]["json"]["data"]["values"]
        assert "domains" in first_values
        # Second call has only "name"
        second_values = mock_post.call_args_list[1][1]["json"]["data"]["values"]
        assert "domains" not in second_values
        assert "name" in second_values

    def test_raises_when_both_attempts_fail(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")

        fail_resp = _fake_response(500, raise_on_raise=True)

        with patch("writeback.attio.requests.post", return_value=fail_resp):
            with pytest.raises(Exception, match="HTTP 500"):
                attio._create_company(make_score())


# ---------------------------------------------------------------------------
# _create_note
# ---------------------------------------------------------------------------

class TestCreateNote:
    def test_posts_to_notes_url(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        ok_resp = _fake_response(200)

        with patch("writeback.attio.requests.post", return_value=ok_resp) as mock_post:
            attio._create_note(make_score(), "rec_123")

        call_url = mock_post.call_args[0][0]
        assert "/notes" in call_url

    def test_calls_raise_for_status(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        ok_resp = _fake_response(200)

        with patch("writeback.attio.requests.post", return_value=ok_resp):
            attio._create_note(make_score(), "rec_123")

        ok_resp.raise_for_status.assert_called_once()

    def test_payload_contains_record_id(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        ok_resp = _fake_response(200)

        with patch("writeback.attio.requests.post", return_value=ok_resp) as mock_post:
            attio._create_note(make_score(), "rec_999")

        payload = mock_post.call_args[1]["json"]
        assert payload["data"]["parent_record_id"] == "rec_999"
        assert payload["data"]["parent_object"] == "companies"


# ---------------------------------------------------------------------------
# upsert_account (integration of company + note)
# ---------------------------------------------------------------------------

class TestUpsertAccount:
    def test_returns_string_with_attio_company_record_id_and_score(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        record_id = "rec_xyz789"
        company_resp = _fake_response(200, {"data": {"id": {"record_id": record_id}}})
        note_resp = _fake_response(200)

        with patch("writeback.attio.requests.post", side_effect=[company_resp, note_resp]):
            result = attio.upsert_account(make_score())

        assert "attio company" in result
        assert record_id in result
        assert "8" in result  # ICP score

    def test_calls_create_note_after_company(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        record_id = "rec_order_check"
        company_resp = _fake_response(200, {"data": {"id": {"record_id": record_id}}})
        note_resp = _fake_response(200)

        with patch("writeback.attio.requests.post", side_effect=[company_resp, note_resp]) as mock_post:
            attio.upsert_account(make_score())

        assert mock_post.call_count == 2
        first_url = mock_post.call_args_list[0][0][0]
        second_url = mock_post.call_args_list[1][0][0]
        assert "/records" in first_url
        assert "/notes" in second_url
