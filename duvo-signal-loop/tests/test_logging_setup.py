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
