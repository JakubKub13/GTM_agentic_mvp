"""Tests for agent_core.run_agent — the provider-driven tool-use loop (async)."""

import inspect
from contextlib import contextmanager
from unittest.mock import patch

from tests.conftest import FakeLLMProvider, make_llm_response, make_tool_call


def _patch_provider(provider):
    """Patch agent_core.get_llm_provider to return the given fake provider."""
    return patch("duvo.agent_core.get_llm_provider", return_value=provider)


def _tool_results(messages):
    """Return all role='tool' result messages from a transcript."""
    return [m for m in messages if m.role == "tool"]


class TestRunAgentNoTools:
    async def test_returns_messages_on_first_text_response(self):
        provider = FakeLLMProvider([make_llm_response(content="hello", stop_reason="stop")])
        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            messages = await run_agent(system="sys", user="hi", tools=[], impls={})

        assert provider.calls == 1
        assert messages[0].role == "user" and messages[0].content == "hi"
        assert [m.role for m in messages] == ["user", "assistant"]

    async def test_system_and_user_reach_the_request(self):
        provider = FakeLLMProvider([make_llm_response(content="x")])
        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            await run_agent("the-system", "the-user", [], {})
        req = provider.requests[0]
        assert req.system == "the-system"
        assert req.messages[0].role == "user" and req.messages[0].content == "the-user"


class TestRunAgentToolExecution:
    async def test_tool_call_then_final_text_returns_full_transcript(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("my_tool", {"x": 1}, "tu-a")],
                    stop_reason="tool_calls",
                ),
                make_llm_response(content="final answer", stop_reason="stop"),
            ]
        )
        seen = {}

        def my_tool(x):
            seen["x"] = x
            return "tool result"

        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            messages = await run_agent("sys", "go", [], impls={"my_tool": my_tool})

        assert provider.calls == 2
        assert seen["x"] == 1
        results = _tool_results(messages)
        assert len(results) == 1
        assert results[0].content == "tool result"
        assert results[0].tool_call_id == "tu-a"
        assert results[0].name == "my_tool"

    async def test_log_captures_tool_call_entries(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("search", {"query": "test"}, "tu-b")],
                    stop_reason="tool_calls",
                ),
                make_llm_response(content="done"),
            ]
        )
        agent_log = []
        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            await run_agent("sys", "go", [], impls={"search": lambda query: "r"}, log=agent_log)
        assert len(agent_log) == 1 and "search" in agent_log[0]

    async def test_async_tool_impl_is_awaited(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("async_tool", {"val": "world"}, "tu-async")],
                    stop_reason="tool_calls",
                ),
                make_llm_response(content="done"),
            ]
        )
        seen = {}

        async def async_tool(val):
            seen["val"] = val
            return "async-result"

        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            messages = await run_agent("sys", "go", [], impls={"async_tool": async_tool})
        assert seen["val"] == "world"
        assert _tool_results(messages)[0].content == "async-result"

    async def test_sync_tool_returning_none_is_not_awaited(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("none_tool", {}, "tu-none")],
                    stop_reason="tool_calls",
                ),
                make_llm_response(content="done"),
            ]
        )
        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            messages = await run_agent("sys", "go", [], impls={"none_tool": lambda: None})
        assert _tool_results(messages)[0].content == "None"
        assert not inspect.isawaitable(None)


class TestRunAgentFinalTools:
    async def test_final_tool_stops_loop(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("finish", {"result": "done"}, "tu-f")],
                    stop_reason="tool_calls",
                ),
            ]
        )
        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            messages = await run_agent(
                "sys", "go", [], impls={"finish": lambda result: result}, final_tools=("finish",)
            )
        assert provider.calls == 1
        assert len(messages) >= 3  # user, assistant, tool

    async def test_non_final_tool_does_not_stop_loop(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("regular", {}, "tu-r")], stop_reason="tool_calls"
                ),
                make_llm_response(
                    tool_calls=[make_tool_call("finish", {}, "tu-f2")], stop_reason="tool_calls"
                ),
            ]
        )
        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            await run_agent(
                "sys",
                "go",
                [],
                impls={"regular": lambda: "r", "finish": lambda: "done"},
                final_tools=("finish",),
            )
        assert provider.calls == 2


class TestRunAgentToolError:
    async def test_tool_error_is_caught_and_fed_back(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("bad_tool", {}, "tu-e")], stop_reason="tool_calls"
                ),
                make_llm_response(content="recovered"),
            ]
        )

        def bad_tool():
            raise ValueError("something went wrong")

        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            messages = await run_agent("sys", "go", [], impls={"bad_tool": bad_tool})
        assert provider.calls == 2
        content = _tool_results(messages)[0].content
        assert "tool error:" in content and "something went wrong" in content


class TestRunAgentMaxTurns:
    async def test_max_turns_respected(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("loop_tool", {}, "tu-loop")],
                    stop_reason="tool_calls",
                ),
            ]
        )
        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            await run_agent("sys", "go", [], impls={"loop_tool": lambda: "x"}, max_turns=3)
        assert provider.calls == 3

    async def test_max_turns_default_is_8(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("loop_tool", {}, "tu-loop2")],
                    stop_reason="tool_calls",
                ),
            ]
        )
        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            await run_agent("sys", "go", [], impls={"loop_tool": lambda: "x"})
        assert provider.calls == 8


class TestRunAgentUnknownTool:
    async def test_unknown_tool_is_labelled_and_loop_continues(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("ghost_tool", {"x": 1}, "tu-ghost")],
                    stop_reason="tool_calls",
                ),
                make_llm_response(content="ok"),
            ]
        )
        with _patch_provider(provider):
            from duvo.agent_core import run_agent

            messages = await run_agent("sys", "go", [], impls={})
        assert provider.calls == 2
        assert _tool_results(messages)[0].content == "unknown tool: ghost_tool"


class TestShortHelper:
    def test_short_basic(self):
        from duvo.agent_core import _short

        assert "key=value" in _short({"key": "value"})

    def test_short_truncates_overall_to_120(self):
        from duvo.agent_core import _short

        assert len(_short({f"key{i}": "v" * 50 for i in range(10)})) <= 120

    def test_short_empty_dict(self):
        from duvo.agent_core import _short

        assert _short({}) == ""

    def test_short_non_dict_does_not_raise(self):
        from duvo.agent_core import _short

        assert isinstance(_short("not a dict"), str)


class _SpyTracing:
    """Stand-in for duvo.agent_core.tracing that records every span() call."""

    def __init__(self):
        self.spans = []

    def obs_for(self, tool_name):
        return ({"exa_search": "retriever"}.get(tool_name, "tool"), "🔧")

    @contextmanager
    def span(self, **kwargs):
        self.spans.append(kwargs)

        class _Rec:
            def update(self, **kw):
                pass

        yield _Rec()


class TestRunAgentTracing:
    async def test_generation_and_tool_spans_are_created(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("exa_search", {"query": "q"}, "tu-1")],
                    stop_reason="tool_calls",
                ),
                make_llm_response(content="final"),
            ]
        )
        spy = _SpyTracing()
        with _patch_provider(provider), patch("duvo.agent_core.tracing", spy):
            from duvo.agent_core import run_agent

            await run_agent("sys", "go", [], impls={"exa_search": lambda query: "r"})

        as_types = [s["as_type"] for s in spy.spans]
        assert "generation" in as_types  # at least one model call span
        # the exa_search tool dispatch became a retriever span with the emoji-prefixed name
        retriever_spans = [s for s in spy.spans if s["as_type"] == "retriever"]
        assert len(retriever_spans) == 1
        assert "exa_search" in retriever_spans[0]["name"]
