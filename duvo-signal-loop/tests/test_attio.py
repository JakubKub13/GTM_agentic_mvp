"""Tests for writeback/attio.py — Attio CRM adapter (async httpx)."""
from unittest.mock import AsyncMock, patch

import pytest

import config
from tests.conftest import _fake_response, make_fake_async_client, make_score
from writeback import attio


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
    async def test_posts_to_records_url_and_returns_record_id(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        record_id = "rec_abc123"
        ok_resp = _fake_response(200, {"data": {"id": {"record_id": record_id}}})
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("http_client.get_client", return_value=client):
            result = await attio._create_company(make_score())

        assert result == record_id
        call_url = client.post.call_args[0][0]
        assert "/objects/companies/records" in call_url

    async def test_retries_with_name_only_when_first_fails(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        record_id = "rec_fallback"

        fail_resp = _fake_response(422)
        ok_resp = _fake_response(200, {"data": {"id": {"record_id": record_id}}})

        client = make_fake_async_client(post=AsyncMock(side_effect=[fail_resp, ok_resp]))

        with patch("http_client.get_client", return_value=client):
            result = await attio._create_company(make_score())

        assert result == record_id
        assert client.post.call_count == 2
        # First call has "domains" in the payload
        first_values = client.post.call_args_list[0][1]["json"]["data"]["values"]
        assert "domains" in first_values
        # Second call has only "name"
        second_values = client.post.call_args_list[1][1]["json"]["data"]["values"]
        assert "domains" not in second_values
        assert "name" in second_values

    async def test_raises_when_both_attempts_fail(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")

        fail_resp = _fake_response(500, raise_on_raise=True)
        client = make_fake_async_client(post=AsyncMock(return_value=fail_resp))

        with patch("http_client.get_client", return_value=client):
            with pytest.raises(Exception, match="HTTP 500"):
                await attio._create_company(make_score())


# ---------------------------------------------------------------------------
# _create_note
# ---------------------------------------------------------------------------

class TestCreateNote:
    async def test_posts_to_notes_url(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("http_client.get_client", return_value=client):
            await attio._create_note(make_score(), "rec_123")

        call_url = client.post.call_args[0][0]
        assert "/notes" in call_url

    async def test_calls_raise_for_status(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("http_client.get_client", return_value=client):
            await attio._create_note(make_score(), "rec_123")

        ok_resp.raise_for_status.assert_called_once()

    async def test_payload_contains_record_id(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        ok_resp = _fake_response(200)
        client = make_fake_async_client(post=AsyncMock(return_value=ok_resp))

        with patch("http_client.get_client", return_value=client):
            await attio._create_note(make_score(), "rec_999")

        payload = client.post.call_args[1]["json"]
        assert payload["data"]["parent_record_id"] == "rec_999"
        assert payload["data"]["parent_object"] == "companies"


# ---------------------------------------------------------------------------
# upsert_account (integration of company + note)
# ---------------------------------------------------------------------------

class TestUpsertAccount:
    async def test_returns_string_with_attio_company_record_id_and_score(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        record_id = "rec_xyz789"
        company_resp = _fake_response(200, {"data": {"id": {"record_id": record_id}}})
        note_resp = _fake_response(200)

        client = make_fake_async_client(post=AsyncMock(side_effect=[company_resp, note_resp]))

        with patch("http_client.get_client", return_value=client):
            result = await attio.upsert_account(make_score())

        assert "attio company" in result
        assert record_id in result
        assert "8" in result  # ICP score

    async def test_calls_create_note_after_company(self, monkeypatch):
        monkeypatch.setattr(config, "ATTIO_API_KEY", "key")
        record_id = "rec_order_check"
        company_resp = _fake_response(200, {"data": {"id": {"record_id": record_id}}})
        note_resp = _fake_response(200)

        client = make_fake_async_client(post=AsyncMock(side_effect=[company_resp, note_resp]))

        with patch("http_client.get_client", return_value=client):
            await attio.upsert_account(make_score())

        assert client.post.call_count == 2
        first_url = client.post.call_args_list[0][0][0]
        second_url = client.post.call_args_list[1][0][0]
        assert "/records" in first_url
        assert "/notes" in second_url
