"""Shared pooled async HTTP client for all write-back modules.

A single :class:`httpx.AsyncClient` instance is lazily created and cached at
the module level so every write-back module shares one connection pool instead
of opening a new pool per request.  Call :func:`aclose` during application
shutdown to drain in-flight connections cleanly.
"""
import httpx

import config
from logging_setup import get_logger

log = get_logger(__name__)

# Module-level cache.  Single-threaded asyncio means no lock is needed — only
# one coroutine runs at a time, so the check-then-set below is atomic enough.
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Return the shared :class:`httpx.AsyncClient`, creating it on first call.

    The client is configured with :data:`config.HTTP_TIMEOUT_SECONDS` so all
    write-back requests share a consistent timeout policy.  Subsequent calls
    return the cached instance without allocating a new connection pool.

    Returns:
        The module-level :class:`httpx.AsyncClient` singleton.
    """
    global _client
    if _client is None:
        log.debug(
            "Creating shared httpx.AsyncClient (timeout=%.1fs)",
            config.HTTP_TIMEOUT_SECONDS,
        )
        _client = httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_SECONDS)
    return _client


async def aclose() -> None:
    """Close the shared :class:`httpx.AsyncClient` and reset the module cache.

    Safe to call when no client has been created yet (no-op).  After this
    returns, the next :func:`get_client` call will create a fresh client.

    This should be awaited during application shutdown to release connection
    pool resources and avoid ``ResourceWarning`` about unclosed sockets.
    """
    global _client
    if _client is not None:
        log.debug("Closing shared httpx.AsyncClient")
        await _client.aclose()
        _client = None
