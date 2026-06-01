"""Tests for logging_setup.py — central logging infrastructure."""
import logging
import pytest


class TestGetLogger:
    def test_get_logger_returns_logger_named_under_duvo_namespace(self):
        """get_logger('x') returns a Logger named 'duvo.x'."""
        from logging_setup import get_logger

        logger = get_logger("x")
        assert logger.name == "duvo.x"

    def test_get_logger_returns_logging_logger_instance(self):
        """get_logger returns a standard logging.Logger."""
        from logging_setup import get_logger

        logger = get_logger("some_module")
        assert isinstance(logger, logging.Logger)

    def test_get_logger_different_names_return_different_loggers(self):
        """Different names give distinct loggers."""
        from logging_setup import get_logger

        a = get_logger("alpha")
        b = get_logger("beta")
        assert a.name != b.name

    def test_get_logger_works_without_explicit_configure_logging(self):
        """get_logger auto-configures the duvo logger if it has no handlers yet.

        Since duvo has propagate=False, caplog cannot intercept its records. We
        instead verify that get_logger() automatically called configure_logging():
        the parent logger must have at least one handler and an effective level
        <= INFO so that INFO records would not be silently dropped.
        """
        # Clear any existing handlers so this test exercises the auto-configure path.
        duvo_logger = logging.getLogger("duvo")
        duvo_logger.handlers.clear()

        from logging_setup import get_logger

        _log = get_logger("bootstrap_test")

        # Calling get_logger must have triggered configure_logging() automatically.
        assert len(duvo_logger.handlers) >= 1, "get_logger must auto-configure a handler"
        assert duvo_logger.level <= logging.INFO, "effective level must be INFO or finer"
        # Handler level must also be INFO or finer so records are not dropped.
        assert duvo_logger.handlers[0].level <= logging.INFO


class TestConfigureLogging:
    def setup_method(self):
        """Remove all handlers from the 'duvo' logger before each test."""
        duvo_logger = logging.getLogger("duvo")
        duvo_logger.handlers.clear()

    def test_configure_logging_adds_handler_to_duvo_logger(self):
        """configure_logging() attaches at least one handler to the 'duvo' logger."""
        from logging_setup import configure_logging

        configure_logging()
        duvo_logger = logging.getLogger("duvo")
        assert len(duvo_logger.handlers) >= 1

    def test_configure_logging_is_idempotent(self):
        """Calling configure_logging() twice does NOT multiply handlers."""
        from logging_setup import configure_logging

        configure_logging()
        configure_logging()
        duvo_logger = logging.getLogger("duvo")
        assert len(duvo_logger.handlers) == 1

    def test_configure_logging_sets_level_on_duvo_logger(self):
        """configure_logging('DEBUG') sets the 'duvo' logger level to DEBUG."""
        from logging_setup import configure_logging

        configure_logging("DEBUG")
        duvo_logger = logging.getLogger("duvo")
        assert duvo_logger.level == logging.DEBUG

    def test_configure_logging_defaults_to_config_log_level(self):
        """configure_logging() with no arg uses config.LOG_LEVEL."""
        import config
        from logging_setup import configure_logging

        configure_logging()
        duvo_logger = logging.getLogger("duvo")
        expected_level = logging.getLevelName(config.LOG_LEVEL)
        assert duvo_logger.level == expected_level

    def test_propagate_is_false(self):
        """The 'duvo' logger must never propagate to the root logger."""
        from logging_setup import configure_logging

        configure_logging()
        duvo_logger = logging.getLogger("duvo")
        assert not duvo_logger.propagate

    def test_handler_level_updated_on_reconfigure(self, caplog):
        """INFO then DEBUG reconfigure leaves handler at DEBUG — records propagate."""
        from logging_setup import configure_logging, get_logger

        configure_logging("INFO")
        configure_logging("DEBUG")

        duvo_logger = logging.getLogger("duvo")
        # The handler's level must also be DEBUG, not stuck at INFO.
        assert duvo_logger.handlers[0].level == logging.DEBUG

    def test_bogus_level_falls_back_to_info(self):
        """An unrecognised level string falls back to INFO (level 20)."""
        from logging_setup import configure_logging

        configure_logging("NONSENSE_LEVEL")
        duvo_logger = logging.getLogger("duvo")
        assert duvo_logger.level == logging.INFO
        assert duvo_logger.handlers[0].level == logging.INFO
