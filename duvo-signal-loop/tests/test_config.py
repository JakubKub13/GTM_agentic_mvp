"""Tests for config.py — require() helper and module-level defaults."""
import pytest


class TestRequireHelper:
    """Tests for the require() guard function."""

    def test_require_returns_value_when_non_empty(self):
        """require() passes through a non-empty value unchanged."""
        from config import require

        result = require("MY_VAR", "some-value")
        assert result == "some-value"

    def test_require_raises_runtime_error_when_empty(self):
        """require() raises RuntimeError when the value is an empty string."""
        from config import require

        with pytest.raises(RuntimeError):
            require("MY_VAR", "")

    def test_require_error_message_contains_var_name(self):
        """RuntimeError message mentions the missing variable name."""
        from config import require

        with pytest.raises(RuntimeError, match="MY_MISSING_VAR"):
            require("MY_MISSING_VAR", "")

    def test_require_raises_runtime_error_for_whitespace_only_value(self):
        """require() rejects whitespace-only values (not just empty string)."""
        from config import require

        with pytest.raises(RuntimeError):
            require("MY_VAR", "   ")


class TestModuleDefaults:
    """Tests for module-level config defaults."""

    def test_crm_provider_defaults_to_attio(self, monkeypatch):
        """CRM_PROVIDER defaults to 'attio' when the env var is absent."""
        monkeypatch.delenv("CRM_PROVIDER", raising=False)
        import importlib
        import config
        importlib.reload(config)
        assert config.CRM_PROVIDER == "attio"

    def test_outreach_provider_defaults_to_brevo(self, monkeypatch):
        """OUTREACH_PROVIDER defaults to 'brevo' when the env var is absent."""
        monkeypatch.delenv("OUTREACH_PROVIDER", raising=False)
        import importlib
        import config
        importlib.reload(config)
        assert config.OUTREACH_PROVIDER == "brevo"

    def test_test_email_default_is_present(self):
        """TEST_EMAIL has a sensible non-empty default."""
        import config
        # The default is jakubkubala3@gmail.com; it must be non-empty
        assert config.TEST_EMAIL != ""

    def test_log_level_default_is_info(self, monkeypatch):
        """LOG_LEVEL defaults to 'INFO' when not set in the environment."""
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        import importlib
        import config
        importlib.reload(config)
        assert config.LOG_LEVEL == "INFO"
