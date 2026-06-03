"""Tests for the LiteLLM-backed provider — mock litellm.acompletion at the boundary."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from duvo.llm.base import LLMRequest, Message, ToolCall, ToolSpec
from duvo.llm.litellm_provider import LiteLLMProvider


def _wire_response(content=None, tool_calls=None, finish_reason="stop"):
    """Build a fake litellm ModelResponse-like object."""
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _wire_tool_call(id_, name, arguments_json):
    return SimpleNamespace(
        id=id_,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments_json),
    )


async def test_translates_system_and_user_to_messages_and_returns_text():
    req = LLMRequest(
        model="openai/gpt-4o", system="be brief", messages=[Message(role="user", content="hi")]
    )
    fake = AsyncMock(return_value=_wire_response(content="hello", finish_reason="stop"))
    with patch("duvo.llm.litellm_provider.litellm.acompletion", fake):
        resp = await LiteLLMProvider().complete(req)

    sent_messages = fake.call_args.kwargs["messages"]
    assert sent_messages[0] == {"role": "system", "content": "be brief"}
    assert sent_messages[1] == {"role": "user", "content": "hi"}
    assert resp.message.content == "hello"
    assert resp.tool_calls == []
    assert resp.stop_reason == "stop"


async def test_tools_are_translated_to_openai_function_shape():
    req = LLMRequest(
        model="openai/gpt-4o",
        system="s",
        messages=[Message(role="user", content="go")],
        tools=[
            ToolSpec(
                name="search", description="d", parameters={"type": "object", "properties": {}}
            )
        ],
    )
    fake = AsyncMock(return_value=_wire_response(content="ok"))
    with patch("duvo.llm.litellm_provider.litellm.acompletion", fake):
        await LiteLLMProvider().complete(req)

    sent_tools = fake.call_args.kwargs["tools"]
    assert sent_tools == [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "d",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert fake.call_args.kwargs["tool_choice"] == "auto"
    assert fake.call_args.kwargs["drop_params"] is True


async def test_parses_tool_calls_with_json_arguments():
    tc = _wire_tool_call("call_1", "search", json.dumps({"query": "duvo"}))
    fake = AsyncMock(return_value=_wire_response(tool_calls=[tc], finish_reason="tool_calls"))
    req = LLMRequest(
        model="openai/gpt-4o", system="s", messages=[Message(role="user", content="go")]
    )
    with patch("duvo.llm.litellm_provider.litellm.acompletion", fake):
        resp = await LiteLLMProvider().complete(req)

    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].id == "call_1"
    assert resp.tool_calls[0].name == "search"
    assert resp.tool_calls[0].arguments == {"query": "duvo"}
    assert resp.message.tool_calls[0].name == "search"


async def test_invalid_tool_arguments_degrade_to_empty_dict():
    tc = _wire_tool_call("call_2", "search", "{not valid json")
    fake = AsyncMock(return_value=_wire_response(tool_calls=[tc], finish_reason="tool_calls"))
    req = LLMRequest(
        model="openai/gpt-4o", system="s", messages=[Message(role="user", content="go")]
    )
    with patch("duvo.llm.litellm_provider.litellm.acompletion", fake):
        resp = await LiteLLMProvider().complete(req)

    assert resp.tool_calls[0].arguments == {}


async def test_assistant_tool_calls_and_tool_results_round_trip_to_wire():
    prior_call = ToolCall(id="call_9", name="search", arguments={"query": "x"})
    req = LLMRequest(
        model="openai/gpt-4o",
        system="s",
        messages=[
            Message(role="user", content="go"),
            Message(role="assistant", content="", tool_calls=[prior_call]),
            Message(role="tool", tool_call_id="call_9", name="search", content="result text"),
        ],
    )
    fake = AsyncMock(return_value=_wire_response(content="done"))
    with patch("duvo.llm.litellm_provider.litellm.acompletion", fake):
        await LiteLLMProvider().complete(req)

    sent = fake.call_args.kwargs["messages"]
    assert sent[2]["role"] == "assistant"
    assert sent[2]["tool_calls"][0]["id"] == "call_9"
    assert sent[2]["tool_calls"][0]["function"]["name"] == "search"
    assert json.loads(sent[2]["tool_calls"][0]["function"]["arguments"]) == {"query": "x"}
    assert sent[3] == {
        "role": "tool",
        "tool_call_id": "call_9",
        "name": "search",
        "content": "result text",
    }


async def test_num_retries_passed_through(monkeypatch):
    from duvo import config

    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 5)
    fake = AsyncMock(return_value=_wire_response(content="ok"))
    req = LLMRequest(
        model="openai/gpt-4o", system="s", messages=[Message(role="user", content="go")]
    )
    with patch("duvo.llm.litellm_provider.litellm.acompletion", fake):
        await LiteLLMProvider().complete(req)
    assert fake.call_args.kwargs["num_retries"] == 5


async def test_custom_base_url_sets_api_base_and_api_key(monkeypatch):
    from duvo import config

    monkeypatch.setattr(config, "LLM_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    fake = AsyncMock(return_value=_wire_response(content="ok"))
    req = LLMRequest(
        model="ollama_chat/llama3.1", system="s", messages=[Message(role="user", content="go")]
    )
    with patch("duvo.llm.litellm_provider.litellm.acompletion", fake):
        await LiteLLMProvider().complete(req)
    assert fake.call_args.kwargs["api_base"] == "http://localhost:11434"
    assert fake.call_args.kwargs["api_key"] == "sk-no-key-required"


async def test_litellm_retries_transient_error(monkeypatch):
    """With acompletion fully mocked, LiteLLM's internal num_retries is bypassed,
    so a transient error surfaces. This documents that our provider does not add
    retries on top of LiteLLM — reliability of LLM calls relies on LiteLLM's
    num_retries in production, and the analyst's conservative-default fallback is
    the backstop for a persistent outage.
    """
    import litellm as _litellm
    import pytest

    from duvo import config

    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 2)
    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _litellm.exceptions.ServiceUnavailableError(
                message="busy",
                llm_provider="openai",
                model="gpt-4o",
            )
        return _wire_response(content="recovered")

    req = LLMRequest(
        model="openai/gpt-4o", system="s", messages=[Message(role="user", content="go")]
    )
    with patch("duvo.llm.litellm_provider.litellm.acompletion", flaky):
        with pytest.raises(_litellm.exceptions.ServiceUnavailableError):
            await LiteLLMProvider().complete(req)
    assert calls["n"] == 1
