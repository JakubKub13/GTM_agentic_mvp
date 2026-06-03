"""Tests for the write-back retry helper."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from duvo.infra import retry
from tests.conftest import _fake_response


def _status_error(status_code, headers=None):
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(status_code, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


async def test_returns_result_on_first_success():
    factory = AsyncMock(return_value="ok")
    with patch("duvo.infra.retry.asyncio.sleep", AsyncMock()):
        result = await retry.with_retries(factory, max_attempts=3)
    assert result == "ok"
    assert factory.call_count == 1


async def test_retries_transient_exception_then_succeeds():
    factory = AsyncMock(side_effect=[_status_error(503), "ok"])
    sleep = AsyncMock()
    with patch("duvo.infra.retry.asyncio.sleep", sleep):
        result = await retry.with_retries(factory, max_attempts=3)
    assert result == "ok"
    assert factory.call_count == 2
    assert sleep.await_count == 1


async def test_permanent_4xx_fails_fast_without_retry():
    factory = AsyncMock(side_effect=_status_error(400))
    sleep = AsyncMock()
    with patch("duvo.infra.retry.asyncio.sleep", sleep):
        with pytest.raises(httpx.HTTPStatusError):
            await retry.with_retries(factory, max_attempts=3)
    assert factory.call_count == 1
    assert sleep.await_count == 0


async def test_raises_after_exhausting_attempts():
    factory = AsyncMock(side_effect=_status_error(500))
    with patch("duvo.infra.retry.asyncio.sleep", AsyncMock()):
        with pytest.raises(httpx.HTTPStatusError):
            await retry.with_retries(factory, max_attempts=2)
    assert factory.call_count == 2


async def test_honors_retry_after_header():
    factory = AsyncMock(side_effect=[_status_error(429, {"Retry-After": "7"}), "ok"])
    sleep = AsyncMock()
    with patch("duvo.infra.retry.asyncio.sleep", sleep):
        await retry.with_retries(factory, max_attempts=3)
    sleep.assert_awaited_once_with(7.0)


async def test_retries_on_timeout_exception():
    factory = AsyncMock(side_effect=[httpx.TimeoutException("slow"), "ok"])
    with patch("duvo.infra.retry.asyncio.sleep", AsyncMock()):
        result = await retry.with_retries(factory, max_attempts=3)
    assert result == "ok"


async def test_retryable_status_on_returned_response_is_retried():
    # Adapters that don't raise (e.g. brevo checks status_code manually) return a
    # Response; a retryable status must trigger a retry, then return the final one.
    factory = AsyncMock(side_effect=[_fake_response(503), _fake_response(200)])
    sleep = AsyncMock()
    with patch("duvo.infra.retry.asyncio.sleep", sleep):
        result = await retry.with_retries(factory, max_attempts=3)
    assert result.status_code == 200
    assert factory.call_count == 2
    assert sleep.await_count == 1
