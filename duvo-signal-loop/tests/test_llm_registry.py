"""Tests for the LLM provider registry (decorator + convention-based lazy lookup)."""

import logging
from contextlib import contextmanager

from duvo.llm import registry
from duvo.llm.base import LLMRequest, LLMResponse, Message


class _Dummy:
    async def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(message=Message(role="assistant"), tool_calls=[], stop_reason="stop")


@contextmanager
def _capture_duvo_logs(level: int = logging.WARNING):
    """Capture log records from the non-propagating 'duvo' logger.

    pytest's caplog cannot intercept records from a logger whose propagate=False.
    Attaches a temporary handler directly to the 'duvo' logger instead.
    """
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _ListHandler(level)
    duvo_logger = logging.getLogger("duvo")
    duvo_logger.addHandler(handler)
    try:
        yield records
    finally:
        duvo_logger.removeHandler(handler)


def test_register_and_get_returns_instance():
    registry.register_llm("dummy")(_Dummy)
    provider = registry.get_llm_provider("dummy")
    assert isinstance(provider, _Dummy)


def test_default_provider_is_litellm(monkeypatch):
    # get_llm_provider(None) must resolve the default ("litellm") by importing its module.
    provider = registry.get_llm_provider(None)
    assert provider.__class__.__name__ == "LiteLLMProvider"


def test_unknown_provider_falls_back_to_default():
    with _capture_duvo_logs(logging.WARNING) as records:
        provider = registry.get_llm_provider("does-not-exist-2")
    assert provider.__class__.__name__ == "LiteLLMProvider"
    assert any("unknown LLM provider" in r.message for r in records)
