"""Tests for http_client.py — shared pooled async HTTP client."""

import httpx
import pytest

from duvo.infra import http_client


@pytest.fixture(autouse=True)
async def reset_client():
    """Reset the module-level client cache before and after each test.

    Ensures test isolation: each test starts with no cached client, and any
    client created during the test is closed cleanly to avoid ResourceWarning.
    """
    # Ensure we start clean regardless of prior test state.
    http_client._client = None
    yield
    # Tear down: close any client left open by the test.
    await http_client.aclose()


class TestGetClient:
    """Tests for get_client()."""

    def test_returns_async_client(self):
        """get_client() returns an httpx.AsyncClient instance."""
        client = http_client.get_client()
        assert isinstance(client, httpx.AsyncClient)

    def test_returns_same_instance_on_second_call(self):
        """get_client() caches and returns the same instance on repeated calls."""
        first = http_client.get_client()
        second = http_client.get_client()
        assert first is second

    def test_client_timeout_reflects_config(self):
        """The client's timeout is configured from config.HTTP_TIMEOUT_SECONDS."""
        from duvo import config

        client = http_client.get_client()
        # httpx stores the timeout as an httpx.Timeout object; its .read attribute
        # (and the others) should match the scalar we passed in.
        assert client.timeout is not None
        # httpx.Timeout exposes .read, .write, .connect, .pool; when constructed
        # from a scalar all four are set to that value.
        assert client.timeout.read == config.HTTP_TIMEOUT_SECONDS
        assert client.timeout.connect == config.HTTP_TIMEOUT_SECONDS


class TestAclose:
    """Tests for aclose()."""

    async def test_aclose_resets_cache_so_new_instance_is_created(self):
        """After aclose(), get_client() creates a fresh client (different identity)."""
        first = http_client.get_client()
        await http_client.aclose()
        second = http_client.get_client()
        assert first is not second

    async def test_aclose_when_no_client_does_not_raise(self):
        """aclose() is a no-op when no client has been created yet."""
        assert http_client._client is None
        # Should not raise.
        await http_client.aclose()

    async def test_aclose_sets_cache_to_none(self):
        """After aclose(), the module cache is None."""
        http_client.get_client()
        assert http_client._client is not None
        await http_client.aclose()
        assert http_client._client is None
