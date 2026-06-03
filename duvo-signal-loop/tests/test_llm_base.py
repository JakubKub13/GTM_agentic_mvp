"""Tests for the provider-neutral LLM contract types."""

from duvo.llm.base import LLMRequest, LLMResponse, Message, ToolCall, ToolSpec, tool_schema


def test_tool_schema_builds_toolspec():
    spec = tool_schema("search", "Search the web", {"type": "object", "properties": {}})
    assert isinstance(spec, ToolSpec)
    assert spec.name == "search"
    assert spec.description == "Search the web"
    assert spec.parameters == {"type": "object", "properties": {}}


def test_message_defaults():
    m = Message(role="user", content="hi")
    assert m.role == "user"
    assert m.content == "hi"
    assert m.tool_calls == []
    assert m.tool_call_id is None
    assert m.name is None


def test_assistant_message_carries_tool_calls():
    tc = ToolCall(id="call_1", name="search", arguments={"query": "x"})
    m = Message(role="assistant", content="", tool_calls=[tc])
    assert m.tool_calls[0].name == "search"
    assert m.tool_calls[0].arguments == {"query": "x"}


def test_llmrequest_and_response_shapes():
    req = LLMRequest(
        model="anthropic/claude-sonnet-4-6",
        system="sys",
        messages=[Message(role="user", content="hi")],
    )
    assert req.tools == []
    assert req.max_tokens == 2000
    tc = ToolCall(id="c1", name="t", arguments={})
    resp = LLMResponse(
        message=Message(role="assistant", tool_calls=[tc]),
        tool_calls=[tc],
        stop_reason="tool_calls",
    )
    assert resp.tool_calls[0].id == "c1"
    assert resp.stop_reason == "tool_calls"
