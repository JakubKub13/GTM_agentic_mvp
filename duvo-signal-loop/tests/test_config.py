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


class TestAsyncKnobs:
    """Tests for the async production knobs added in the async migration."""

    def test_max_concurrent_accounts_exists_and_is_int(self):
        """MAX_CONCURRENT_ACCOUNTS is present and is an int."""
        import config
        assert hasattr(config, "MAX_CONCURRENT_ACCOUNTS")
        assert isinstance(config.MAX_CONCURRENT_ACCOUNTS, int)

    def test_max_concurrent_accounts_default(self, monkeypatch):
        """MAX_CONCURRENT_ACCOUNTS defaults to 5 when env var is absent."""
        monkeypatch.delenv("MAX_CONCURRENT_ACCOUNTS", raising=False)
        import importlib
        import config
        importlib.reload(config)
        assert config.MAX_CONCURRENT_ACCOUNTS == 5

    def test_http_timeout_seconds_exists_and_is_float(self):
        """HTTP_TIMEOUT_SECONDS is present and is a float."""
        import config
        assert hasattr(config, "HTTP_TIMEOUT_SECONDS")
        assert isinstance(config.HTTP_TIMEOUT_SECONDS, float)

    def test_http_timeout_seconds_default(self, monkeypatch):
        """HTTP_TIMEOUT_SECONDS defaults to 30.0 when env var is absent."""
        monkeypatch.delenv("HTTP_TIMEOUT_SECONDS", raising=False)
        import importlib
        import config
        importlib.reload(config)
        assert config.HTTP_TIMEOUT_SECONDS == 30.0

    def test_anthropic_timeout_seconds_exists_and_is_float(self):
        """ANTHROPIC_TIMEOUT_SECONDS is present and is a float."""
        import config
        assert hasattr(config, "ANTHROPIC_TIMEOUT_SECONDS")
        assert isinstance(config.ANTHROPIC_TIMEOUT_SECONDS, float)

    def test_anthropic_timeout_seconds_default(self, monkeypatch):
        """ANTHROPIC_TIMEOUT_SECONDS defaults to 120.0 when env var is absent."""
        monkeypatch.delenv("ANTHROPIC_TIMEOUT_SECONDS", raising=False)
        import importlib
        import config
        importlib.reload(config)
        assert config.ANTHROPIC_TIMEOUT_SECONDS == 120.0

    def test_account_timeout_seconds_exists_and_is_float(self):
        """ACCOUNT_TIMEOUT_SECONDS is present and is a float."""
        import config
        assert hasattr(config, "ACCOUNT_TIMEOUT_SECONDS")
        assert isinstance(config.ACCOUNT_TIMEOUT_SECONDS, float)

    def test_account_timeout_seconds_default(self, monkeypatch):
        """ACCOUNT_TIMEOUT_SECONDS defaults to 300.0 (5 min) when env var is absent."""
        monkeypatch.delenv("ACCOUNT_TIMEOUT_SECONDS", raising=False)
        import importlib
        import config
        importlib.reload(config)
        assert config.ACCOUNT_TIMEOUT_SECONDS == 300.0
