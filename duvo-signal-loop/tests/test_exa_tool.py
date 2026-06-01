"""Tests for tools/exa_tool.py — exa_search tool and EXA_SEARCH_TOOL schema."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exa_result(title="Test Title", published_date="2024-01-01",
                     url="https://example.com", summary="A summary."):
    return SimpleNamespace(
        title=title,
        published_date=published_date,
        url=url,
        summary=summary,
    )


def _make_exa_response(results):
    return SimpleNamespace(results=results)


@pytest.fixture()
def fake_exa():
    exa = MagicMock()
    return exa


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestExaSearchToolSchema:
    """EXA_SEARCH_TOOL schema must have the exact shape agents depend on."""

    def test_schema_name(self):
        from tools.exa_tool import EXA_SEARCH_TOOL
        assert EXA_SEARCH_TOOL["name"] == "exa_search"

    def test_schema_has_description(self):
        from tools.exa_tool import EXA_SEARCH_TOOL
        assert "description" in EXA_SEARCH_TOOL
        assert len(EXA_SEARCH_TOOL["description"]) > 10

    def test_schema_input_schema_type_object(self):
        from tools.exa_tool import EXA_SEARCH_TOOL
        assert EXA_SEARCH_TOOL["input_schema"]["type"] == "object"

    def test_schema_has_required_query(self):
        from tools.exa_tool import EXA_SEARCH_TOOL
        assert "query" in EXA_SEARCH_TOOL["input_schema"]["required"]

    def test_schema_has_query_property(self):
        from tools.exa_tool import EXA_SEARCH_TOOL
        props = EXA_SEARCH_TOOL["input_schema"]["properties"]
        assert "query" in props
        assert props["query"]["type"] == "string"

    def test_schema_has_start_published_date_property(self):
        from tools.exa_tool import EXA_SEARCH_TOOL
        props = EXA_SEARCH_TOOL["input_schema"]["properties"]
        assert "start_published_date" in props
        assert props["start_published_date"]["type"] == "string"

    def test_start_published_date_not_required(self):
        from tools.exa_tool import EXA_SEARCH_TOOL
        required = EXA_SEARCH_TOOL["input_schema"].get("required", [])
        assert "start_published_date" not in required


# ---------------------------------------------------------------------------
# exa_search function tests
# ---------------------------------------------------------------------------

class TestExaSearch:
    """exa_search() returns formatted strings and handles edge cases."""

    def test_formats_results_with_title_date_url_summary(self, fake_exa):
        fake_exa.search_and_contents.return_value = _make_exa_response([
            _make_exa_result(
                title="My Title",
                published_date="2024-06-15",
                url="https://news.example.com/article",
                summary="Short summary here.",
            )
        ])

        with patch("tools.exa_tool._get_exa", return_value=fake_exa):
            from tools.exa_tool import exa_search
            result = exa_search("test query")

        assert "TITLE: My Title" in result
        assert "DATE: 2024-06-15" in result
        assert "URL: https://news.example.com/article" in result
        assert "SUMMARY: Short summary here." in result

    def test_no_results_returns_no_results_string(self, fake_exa):
        fake_exa.search_and_contents.return_value = _make_exa_response([])

        with patch("tools.exa_tool._get_exa", return_value=fake_exa):
            from tools.exa_tool import exa_search
            result = exa_search("empty query")

        assert result == "no results"

    def test_multiple_results_all_formatted(self, fake_exa):
        fake_exa.search_and_contents.return_value = _make_exa_response([
            _make_exa_result(title="First"),
            _make_exa_result(title="Second"),
            _make_exa_result(title="Third"),
        ])

        with patch("tools.exa_tool._get_exa", return_value=fake_exa):
            from tools.exa_tool import exa_search
            result = exa_search("multi query")

        assert "TITLE: First" in result
        assert "TITLE: Second" in result
        assert "TITLE: Third" in result

    def test_search_exception_returns_search_failed_string(self, fake_exa):
        fake_exa.search_and_contents.side_effect = ConnectionError("network error")

        with patch("tools.exa_tool._get_exa", return_value=fake_exa):
            from tools.exa_tool import exa_search
            result = exa_search("bad query")

        assert result.startswith("search failed:")
        assert "network error" in result

    def test_start_published_date_passed_to_search(self, fake_exa):
        fake_exa.search_and_contents.return_value = _make_exa_response([])

        with patch("tools.exa_tool._get_exa", return_value=fake_exa):
            from tools.exa_tool import exa_search
            exa_search("query with date", start_published_date="2024-01-01")

        _, kwargs = fake_exa.search_and_contents.call_args
        assert kwargs.get("start_published_date") == "2024-01-01"

    def test_no_start_published_date_not_in_kwargs(self, fake_exa):
        fake_exa.search_and_contents.return_value = _make_exa_response([])

        with patch("tools.exa_tool._get_exa", return_value=fake_exa):
            from tools.exa_tool import exa_search
            exa_search("plain query")

        _, kwargs = fake_exa.search_and_contents.call_args
        assert "start_published_date" not in kwargs

    def test_summary_truncated_to_400_chars(self, fake_exa):
        long_summary = "x" * 600
        fake_exa.search_and_contents.return_value = _make_exa_response([
            _make_exa_result(summary=long_summary),
        ])

        with patch("tools.exa_tool._get_exa", return_value=fake_exa):
            from tools.exa_tool import exa_search
            result = exa_search("long summary query")

        # Find the SUMMARY line and check its length
        for line in result.split("\n"):
            if "SUMMARY:" in line:
                summary_value = line.split("SUMMARY:", 1)[1].strip()
                assert len(summary_value) <= 400
                break

    def test_none_title_shows_none_placeholder(self, fake_exa):
        fake_exa.search_and_contents.return_value = _make_exa_response([
            _make_exa_result(title=None),
        ])

        with patch("tools.exa_tool._get_exa", return_value=fake_exa):
            from tools.exa_tool import exa_search
            result = exa_search("query")

        assert "TITLE: (none)" in result

    def test_none_date_shows_unknown(self, fake_exa):
        fake_exa.search_and_contents.return_value = _make_exa_response([
            _make_exa_result(published_date=None),
        ])

        with patch("tools.exa_tool._get_exa", return_value=fake_exa):
            from tools.exa_tool import exa_search
            result = exa_search("query")

        assert "DATE: unknown" in result

    def test_returns_string_type(self, fake_exa):
        fake_exa.search_and_contents.return_value = _make_exa_response([
            _make_exa_result(),
        ])

        with patch("tools.exa_tool._get_exa", return_value=fake_exa):
            from tools.exa_tool import exa_search
            result = exa_search("query")

        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Lazy init tests
# ---------------------------------------------------------------------------

class TestExaLazyInit:
    """_get_exa() builds the client lazily; module import does not require a key."""

    def test_module_imports_without_api_key(self, monkeypatch):
        """Importing tools.exa_tool must not raise even if EXA_API_KEY is empty."""
        import importlib
        import tools.exa_tool
        # If we got here without RuntimeError, the lazy init works
        assert hasattr(tools.exa_tool, "exa_search")

    def test_get_exa_raises_when_key_missing(self, monkeypatch):
        """_get_exa() must raise RuntimeError when EXA_API_KEY is empty."""
        import tools.exa_tool
        # Reset the cached client so _get_exa() re-runs initialisation
        monkeypatch.setattr("tools.exa_tool._exa", None)

        with patch("tools.exa_tool.EXA_API_KEY", ""):
            with pytest.raises(RuntimeError):
                tools.exa_tool._get_exa()
