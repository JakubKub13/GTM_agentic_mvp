"""Tests for agent_core.py — the reusable Anthropic tool-use loop."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Helpers to build fake Anthropic response objects
# ---------------------------------------------------------------------------

def _make_text_block(text="done"):
    return SimpleNamespace(type="text", text=text)


def _make_tool_use_block(name, input_dict, id_="tu-1"):
    return SimpleNamespace(type="tool_use", name=name, input=input_dict, id=id_)


def _make_response(content):
    """Build a fake messages.create response with a .content list."""
    return SimpleNamespace(content=content)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_client():
    """A fake Anthropic client whose messages.create can be configured per test."""
    client = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunAgentNoTools:
    """When the model returns no tool_use blocks, the loop returns immediately."""

    def test_returns_messages_on_first_text_response(self, fake_client):
        fake_client.messages.create.return_value = _make_response([_make_text_block("hello")])

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            messages = run_agent(
                system="sys",
                user="hi",
                tools=[],
                impls={},
            )

        # Only one create call; messages has user + assistant turn
        fake_client.messages.create.assert_called_once()
        assert messages[0] == {"role": "user", "content": "hi"}
        roles = [m["role"] for m in messages]
        assert roles == ["user", "assistant"]

    def test_messages_list_starts_with_user_message(self, fake_client):
        fake_client.messages.create.return_value = _make_response([_make_text_block()])

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            messages = run_agent("sys", "user prompt", [], {})

        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "user prompt"


class TestRunAgentToolExecution:
    """Tool calls are dispatched, results fed back, and the loop continues."""

    def test_tool_call_then_final_text_returns_full_transcript(self, fake_client):
        tool_block = _make_tool_use_block("my_tool", {"x": 1}, "tu-a")
        fake_client.messages.create.side_effect = [
            _make_response([tool_block]),
            _make_response([_make_text_block("final answer")]),
        ]

        impl_called_with = {}

        def my_tool(x):
            impl_called_with["x"] = x
            return "tool result"

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            messages = run_agent(
                system="sys",
                user="go",
                tools=[],
                impls={"my_tool": my_tool},
            )

        assert fake_client.messages.create.call_count == 2
        assert impl_called_with["x"] == 1
        # The tool result should appear somewhere in messages (content items are dicts with "type")
        tool_result_msgs = [
            m for m in messages
            if isinstance(m.get("content"), list)
            and all(isinstance(r, dict) for r in m["content"])
            and any(r.get("type") == "tool_result" for r in m["content"])
        ]
        assert len(tool_result_msgs) == 1

    def test_log_captures_tool_call_entries(self, fake_client):
        tool_block = _make_tool_use_block("search", {"query": "test"}, "tu-b")
        fake_client.messages.create.side_effect = [
            _make_response([tool_block]),
            _make_response([_make_text_block()]),
        ]

        agent_log = []
        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            run_agent("sys", "go", [], impls={"search": lambda query: "r"}, log=agent_log)

        assert len(agent_log) == 1
        assert "search" in agent_log[0]

    def test_log_is_none_by_default_no_error(self, fake_client):
        tool_block = _make_tool_use_block("t", {}, "tu-c")
        fake_client.messages.create.side_effect = [
            _make_response([tool_block]),
            _make_response([_make_text_block()]),
        ]

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            # Should not raise
            run_agent("sys", "go", [], impls={"t": lambda: "ok"})


class TestRunAgentFinalTools:
    """Reaching a final tool causes an immediate return without waiting for the next model call."""

    def test_final_tool_stops_loop(self, fake_client):
        final_block = _make_tool_use_block("finish", {"result": "done"}, "tu-f")
        fake_client.messages.create.return_value = _make_response([final_block])

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            messages = run_agent(
                system="sys",
                user="go",
                tools=[],
                impls={"finish": lambda result: result},
                final_tools=("finish",),
            )

        # Only one create call — loop exits after the final tool
        fake_client.messages.create.assert_called_once()
        # Messages should include the tool result turn
        assert len(messages) >= 3  # user, assistant, tool_result

    def test_non_final_tool_does_not_stop_loop(self, fake_client):
        regular_block = _make_tool_use_block("regular", {}, "tu-r")
        final_block = _make_tool_use_block("finish", {}, "tu-f2")
        fake_client.messages.create.side_effect = [
            _make_response([regular_block]),
            _make_response([final_block]),
            _make_response([_make_text_block()]),  # should not be reached
        ]

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            run_agent(
                system="sys",
                user="go",
                tools=[],
                impls={"regular": lambda: "r", "finish": lambda: "done"},
                final_tools=("finish",),
            )

        # Two create calls: one for each tool turn; stops after finish
        assert fake_client.messages.create.call_count == 2


class TestRunAgentToolError:
    """When a tool impl raises, the error is caught and fed back as a tool_result."""

    def test_tool_error_is_caught_and_fed_back(self, fake_client):
        tool_block = _make_tool_use_block("bad_tool", {}, "tu-e")
        fake_client.messages.create.side_effect = [
            _make_response([tool_block]),
            _make_response([_make_text_block("recovered")]),
        ]

        def bad_tool():
            raise ValueError("something went wrong")

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            messages = run_agent("sys", "go", [], impls={"bad_tool": bad_tool})

        # Loop must continue (two create calls)
        assert fake_client.messages.create.call_count == 2

        # The error text must appear in the tool_result content (content items are dicts)
        tool_result_turns = [
            m for m in messages
            if isinstance(m.get("content"), list)
            and all(isinstance(r, dict) for r in m["content"])
            and any(r.get("type") == "tool_result" for r in m["content"])
        ]
        assert tool_result_turns, "Expected a tool_result turn in messages"
        result_content = tool_result_turns[0]["content"][0]["content"]
        assert "tool error:" in result_content
        assert "something went wrong" in result_content

    def test_tool_error_does_not_stop_loop(self, fake_client):
        """An erroring tool is not treated as a final tool."""
        tool_block = _make_tool_use_block("fail", {}, "tu-fail")
        fake_client.messages.create.side_effect = [
            _make_response([tool_block]),
            _make_response([_make_text_block("ok")]),
        ]

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            msgs = run_agent("sys", "go", [], impls={"fail": lambda: (_ for _ in ()).throw(RuntimeError("boom"))})

        assert fake_client.messages.create.call_count == 2


class TestRunAgentMaxTurns:
    """max_turns caps the number of model calls."""

    def test_max_turns_respected(self, fake_client):
        # Always return a tool_use so the loop would run forever without the cap
        tool_block = _make_tool_use_block("loop_tool", {}, "tu-loop")
        fake_client.messages.create.return_value = _make_response([tool_block])

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            run_agent("sys", "go", [], impls={"loop_tool": lambda: "x"}, max_turns=3)

        assert fake_client.messages.create.call_count == 3

    def test_max_turns_default_is_8(self, fake_client):
        tool_block = _make_tool_use_block("loop_tool", {}, "tu-loop2")
        fake_client.messages.create.return_value = _make_response([tool_block])

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            run_agent("sys", "go", [], impls={"loop_tool": lambda: "x"})

        assert fake_client.messages.create.call_count == 8


class TestRunAgentUnknownTool:
    """When the model calls a tool name not in impls, the loop handles it gracefully."""

    def test_unknown_tool_does_not_crash(self, fake_client):
        unknown_block = _make_tool_use_block("ghost_tool", {"x": 1}, "tu-ghost")
        fake_client.messages.create.side_effect = [
            _make_response([unknown_block]),
            _make_response([_make_text_block("ok")]),
        ]

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            # Must not raise
            messages = run_agent("sys", "go", [], impls={})

        # Loop should continue and terminate normally (two create calls)
        assert fake_client.messages.create.call_count == 2

    def test_unknown_tool_result_content_is_clearly_labelled(self, fake_client):
        unknown_block = _make_tool_use_block("ghost_tool", {"x": 1}, "tu-ghost2")
        fake_client.messages.create.side_effect = [
            _make_response([unknown_block]),
            _make_response([_make_text_block("done")]),
        ]

        with patch("agent_core._get_client", return_value=fake_client):
            from agent_core import run_agent
            messages = run_agent("sys", "go", [], impls={})

        # Find tool_result turns
        tool_result_turns = [
            m for m in messages
            if isinstance(m.get("content"), list)
            and any(isinstance(r, dict) and r.get("type") == "tool_result" for r in m["content"])
        ]
        assert tool_result_turns, "Expected a tool_result turn in messages"
        result_content = tool_result_turns[0]["content"][0]["content"]
        assert result_content == "unknown tool: ghost_tool"


class TestShortHelper:
    """_short() truncates long dict values and joins items."""

    def test_short_basic(self):
        from agent_core import _short
        result = _short({"key": "value"})
        assert "key=value" in result

    def test_short_truncates_long_values(self):
        from agent_core import _short
        long_val = "x" * 100
        result = _short({"k": long_val})
        # The value should be truncated to 40 chars
        assert len(result) <= 120
        # k= prefix should appear
        assert "k=" in result

    def test_short_truncates_overall_to_120(self):
        from agent_core import _short
        # Many items so the joined string would exceed 120
        big_dict = {f"key{i}": "v" * 50 for i in range(10)}
        result = _short(big_dict)
        assert len(result) <= 120

    def test_short_empty_dict(self):
        from agent_core import _short
        result = _short({})
        assert result == ""

    def test_short_non_dict_does_not_raise(self):
        from agent_core import _short
        # Should not raise for non-dict input
        result = _short("not a dict")
        assert isinstance(result, str)
