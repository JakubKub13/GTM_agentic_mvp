"""Central logging infrastructure for the duvo-signal-loop project.

Every module in this project should obtain its logger via::

    from logging_setup import get_logger
    log = get_logger(__name__)

This keeps all project logs under a single ``duvo`` parent logger so that the
log level and handlers can be configured once (e.g. from ``LOG_LEVEL`` in
``.env``) and inherited by every child logger automatically.
"""
import logging

import config

# Name of the shared parent logger for the entire project.
_ROOT_LOGGER_NAME = "duvo"


def configure_logging(level: str | None = None) -> None:
    """Configure the root ``duvo`` logger with a readable format.

    This function is idempotent — calling it more than once does **not** attach
    duplicate handlers.  It is safe to call at module import time.

    Args:
        level: A log-level string such as ``"DEBUG"``, ``"INFO"``, or
            ``"WARNING"``.  When *None* (the default) the value of
            ``config.LOG_LEVEL`` is used, which itself falls back to ``"INFO"``.
    """
    resolved_level = level if level is not None else config.LOG_LEVEL
    duvo_logger = logging.getLogger(_ROOT_LOGGER_NAME)

    # Idempotency guard — do not add a second handler if we already have one.
    if duvo_logger.handlers:
        # Still apply the (possibly new) level in case it was requested explicitly.
        duvo_logger.setLevel(resolved_level)
        return

    duvo_logger.setLevel(resolved_level)

    handler = logging.StreamHandler()
    handler.setLevel(resolved_level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    duvo_logger.addHandler(handler)

    # Prevent logs from bubbling up to the root logger (avoids duplicate output
    # when the root logger also has a StreamHandler configured).
    duvo_logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``duvo`` namespace.

    Args:
        name: Typically ``__name__`` of the calling module.  The resulting
            logger will be named ``duvo.<name>``.

    Returns:
        A :class:`logging.Logger` instance whose effective level and handlers
        are inherited from the ``duvo`` parent logger.
    """
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")
