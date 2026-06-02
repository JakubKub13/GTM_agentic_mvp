"""Reusable Anthropic tool-use loop — the runtime every agent in the system runs on."""
import inspect
from collections.abc import Callable, Iterable

from anthropic import AsyncAnthropic

from duvo.config import ANTHROPIC_API_KEY, ANTHROPIC_TIMEOUT_SECONDS, CLAUDE_MODEL, require
from duvo.infra.logging_setup import get_logger

_log = get_logger(__name__)

_client = None


def _get_client() -> AsyncAnthropic:
    """Return the shared AsyncAnthropic client, initialising it on first use.

    Lazy init means the module can be imported in tests without a real API key;
    the key is only validated when an actual API call is made.

    Lazy init is safe: the event loop is single-threaded and there is no await
    between the None-check and assignment, so no concurrent double-construct can occur.
    """
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=require("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            timeout=ANTHROPIC_TIMEOUT_SECONDS,
        )
    return _client


async def run_agent(
    system: str,
    user: str,
    tools: list[dict],
    impls: dict[str, Callable],
    max_turns: int = 8,
    final_tools: Iterable[str] = (),
    log: list[str] | None = None,
    max_tokens: int = 2000,
) -> list:
    """Drive a tool-using Anthropic agent to completion (async).

    Calls the model, dispatches any ``tool_use`` blocks through *impls*, feeds
    results back, and repeats until a final tool is called, the model returns
    no tool calls, or *max_turns* is exhausted.

    Both synchronous and asynchronous tool impls are supported: if calling an
    impl returns an awaitable it will be awaited automatically.

    Args:
        system:      System prompt string.
        user:        Initial user message string.
        tools:       List of Anthropic tool schema dicts to pass to the model.
        impls:       Mapping of tool name → callable (sync or async); called
                     for each tool_use block.
        max_turns:   Maximum number of model calls (default 8).
        final_tools: Iterable of tool names that, when called, terminate the loop.
        log:         Optional list; each tool call appends a short entry for the
                     audit report (e.g. ``"tool_name(arg=val, ...)``).
        max_tokens:  Max output tokens per model call (default 2000). Agents that
                     emit large final tool payloads (e.g. the analyst's full
                     assessment + outreach draft) should raise this so the tool
                     call is not truncated mid-JSON.

    Returns:
        The full messages list (conversation transcript).
    """
    final = set(final_tools)
    messages = [{"role": "user", "content": user}]

    for turn in range(max_turns):
        _log.debug("agent turn %d / %d", turn + 1, max_turns)

        resp = await _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
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
            _log.info("tool called: %s(%s)", tu.name, _short(tu.input))

            if log is not None:
                log.append(f"{tu.name}({_short(tu.input)})")

            if tu.name not in impls:
                _log.warning("model called unknown tool: %s", tu.name)
                output = f"unknown tool: {tu.name}"
            else:
                try:
                    output = impls[tu.name](**tu.input)
                    if inspect.isawaitable(output):
                        output = await output
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
