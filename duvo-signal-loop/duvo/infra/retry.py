"""Async retry helper for write-back HTTP calls.

Retries only transient failures (connection errors, timeouts, and the
retryable HTTP statuses 408/425/429/5xx), with capped exponential backoff plus
jitter, honoring ``Retry-After`` when present. Permanent errors (e.g. 4xx other
than 408/425/429) fail fast. Used by the write-back adapters; LLM-call retries
are handled separately by LiteLLM's ``num_retries``.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from duvo.infra.logging_setup import get_logger

_log = get_logger(__name__)

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _status_of(obj: Any) -> int | None:
    """Return an HTTP status code from an exception or a response-like object, else None.

    Duck-types ``.status_code`` so it works with both real ``httpx.Response``
    objects and the test suite's ``_fake_response`` (a ``MagicMock`` with an int
    ``status_code``). Only an ``int`` is accepted — never a stray mock attribute.
    """
    if isinstance(obj, httpx.HTTPStatusError):
        return obj.response.status_code
    status = getattr(obj, "status_code", None)
    return status if isinstance(status, int) else None


def _is_transient_exc(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def _retry_after(obj: Any) -> float | None:
    """Extract a numeric ``Retry-After`` (seconds) from an exception or Response."""
    response = None
    if isinstance(obj, httpx.HTTPStatusError):
        response = obj.response
    elif isinstance(obj, httpx.Response):
        response = obj
    if response is not None:
        value = response.headers.get("Retry-After")
        if value and value.strip().isdigit():
            return float(value.strip())
    return None


def _backoff(attempt: int, base_delay: float, max_delay: float) -> float:
    """Capped exponential backoff with up to 10% jitter."""
    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return delay + random.uniform(0, delay * 0.1)


async def with_retries(
    factory: Callable[[], Awaitable[Any]],
    *,
    max_attempts: int,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
) -> Any:
    """Call ``factory()`` with retries on transient failures.

    ``factory`` must be a zero-arg coroutine factory (e.g.
    ``lambda: client.post(url, json=payload, headers=h)``) so it can be retried.
    A retryable HTTP status on a *returned* Response also triggers a retry; the
    final Response (or successful result) is returned to the caller.

    Args:
        factory:      Zero-arg async callable performing the request.
        max_attempts: Total attempts (>= 1).
        base_delay:   First backoff in seconds.
        max_delay:    Backoff ceiling in seconds.

    Returns:
        The factory's result (the last one if all attempts hit a retryable status).

    Raises:
        The last exception when a transient error persists past *max_attempts*,
        or immediately for a non-transient exception.
    """
    result: Any = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await factory()
        except Exception as exc:
            if not _is_transient_exc(exc) or attempt == max_attempts:
                raise
            delay = _retry_after(exc) or _backoff(attempt, base_delay, max_delay)
            _log.warning(
                "transient error (attempt %d/%d): %s — retrying in %.1fs",
                attempt,
                max_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        status = _status_of(result)
        if status in _RETRYABLE_STATUS and attempt < max_attempts:
            delay = _retry_after(result) or _backoff(attempt, base_delay, max_delay)
            _log.warning(
                "retryable status %s (attempt %d/%d) — retrying in %.1fs",
                status,
                attempt,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)
            continue
        return result
    return result
