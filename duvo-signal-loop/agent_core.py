"""Reusable Anthropic tool-use loop — the runtime every agent in the system runs on."""
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, require
from logging_setup import get_logger

_log = get_logger(__name__)

_client = None


def _get_client() -> Anthropic:
    """Return the shared Anthropic client, initialising it on first use.

    Lazy init means the module can be imported in tests without a real API key;
    the key is only validated when an actual API call is made.
    """
    global _client
    if _client is None:
        _client = Anthropic(api_key=require("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY))
    return _client


def run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
    """Drive a tool-using Anthropic agent to completion.

    Calls the model, dispatches any ``tool_use`` blocks through *impls*, feeds
    results back, and repeats until a final tool is called, the model returns
    no tool calls, or *max_turns* is exhausted.

    Args:
        system:      System prompt string.
        user:        Initial user message string.
        tools:       List of Anthropic tool schema dicts to pass to the model.
        impls:       Mapping of tool name → callable; called for each tool_use block.
        max_turns:   Maximum number of model calls (default 8).
        final_tools: Iterable of tool names that, when called, terminate the loop.
        log:         Optional list; each tool call appends a short entry for the
                     audit report (e.g. ``"tool_name(arg=val, ...)``).

    Returns:
        The full messages list (conversation transcript).
    """
    final = set(final_tools)
    messages = [{"role": "user", "content": user}]

    for turn in range(max_turns):
        _log.debug("agent turn %d / %d", turn + 1, max_turns)

        resp = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=system,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            _log.debug("loop ended: no tool_use blocks in response (turn %d)", turn + 1)
            return messages

        results = []
        hit_final = False
        for tu in tool_uses:
            _log.info("tool called: %s", tu.name)
            _log.debug("tool args: %s", _short(tu.input))

            if log is not None:
                log.append(f"{tu.name}({_short(tu.input)})")

            try:
                output = impls[tu.name](**tu.input)
            except Exception as exc:
                _log.warning("tool %s raised: %s", tu.name, exc)
                output = f"tool error: {exc}"

            results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": str(output),
            })
            if tu.name in final:
                hit_final = True

        messages.append({"role": "user", "content": results})

        if hit_final:
            _log.debug("loop ended: final tool reached (turn %d)", turn + 1)
            return messages

    _log.warning(
        "loop ended: max_turns=%d reached without a final tool — transcript may be incomplete",
        max_turns,
    )
    return messages


def _short(d) -> str:
    """Render a tool-input dict as a short, readable string (≤ 120 chars).

    Args:
        d: The tool input dict (or any object).

    Returns:
        A compact string representation suitable for log lines.
    """
    try:
        items = ", ".join(f"{k}={str(v)[:40]}" for k, v in d.items())
    except Exception:
        items = ""
    return items[:120]
