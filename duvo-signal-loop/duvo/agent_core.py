"""Reusable tool-use loop — the runtime every agent in the system runs on.

Provider-agnostic: the loop speaks only the neutral types in ``duvo.llm.base``
and delegates the actual model call to a registered :class:`LLMProvider`
(default: LiteLLM). No wire format (OpenAI/Anthropic) appears here.
"""

import inspect
from collections.abc import Callable

from duvo.config import LLM_MODEL, LLM_PROVIDER
from duvo.infra import events, tracing
from duvo.infra.logging_setup import get_logger
from duvo.llm.base import LLMRequest, Message, ToolSpec
from duvo.llm.registry import get_llm_provider

_log = get_logger(__name__)


async def run_agent(
    system: str,
    user: str,
    tools: list[ToolSpec],
    impls: dict[str, Callable],
    max_turns: int = 8,
    final_tools=(),
    log: list[str] | None = None,
    max_tokens: int = 2000,
) -> list[Message]:
    """Drive a tool-using agent to completion (async, provider-agnostic).

    Calls the configured LLM provider, dispatches any tool calls through
    *impls*, feeds results back, and repeats until a final tool is called, the
    model returns no tool calls, or *max_turns* is exhausted.

    Both synchronous and asynchronous tool impls are supported: if calling an
    impl returns an awaitable it is awaited automatically.

    Args:
        system:      System prompt string.
        user:        Initial user message string.
        tools:       List of :class:`~duvo.llm.base.ToolSpec` for the model.
        impls:       Mapping of tool name → callable (sync or async).
        max_turns:   Maximum number of model calls (default 8).
        final_tools: Tool names that, when called, terminate the loop.
        log:         Optional list; each tool call appends a short audit entry.
        max_tokens:  Max output tokens per model call (default 2000).

    Returns:
        The full transcript as a list of :class:`~duvo.llm.base.Message`.
    """
    final = set(final_tools)
    provider = get_llm_provider(LLM_PROVIDER)
    messages: list[Message] = [Message(role="user", content=user)]

    for turn in range(max_turns):
        _log.debug("agent turn %d / %d", turn + 1, max_turns)

        request = LLMRequest(
            model=LLM_MODEL,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )
        with tracing.span(
            name=f"💬 turn-{turn + 1}",
            as_type="generation",
            model=LLM_MODEL,
            model_parameters={"max_tokens": max_tokens},
            input=[{"role": m.role, "content": m.content} for m in messages],
        ) as gen:
            response = await provider.complete(request)
            if gen is not None:
                gen.update(
                    output=response.message.content,
                    usage_details=response.usage,
                    cost_details=(
                        {"total": response.cost_usd} if response.cost_usd is not None else None
                    ),
                )
        messages.append(response.message)

        if not response.tool_calls:
            _log.debug("loop ended: no tool calls in response (turn %d)", turn + 1)
            return messages

        hit_final = False
        for tc in response.tool_calls:
            _log.info("tool called: %s(%s)", tc.name, _short(tc.arguments))
            if log is not None:
                log.append(f"{tc.name}({_short(tc.arguments)})")

            # Plan #9: publish a bounded, redacted tool event to the live feed. The
            # producer context (run_id/account/agent/beat) is set upstream via a
            # contextvar; with no SSE subscribers this is a silent no-op (CLI/offline).
            ctx = events.get_context()
            if ctx.get("run_id"):
                events.publish(
                    ctx["run_id"],
                    events.make_tool_event(tool=tc.name, arguments=tc.arguments),
                )

            as_type, emoji = tracing.obs_for(tc.name)
            with tracing.span(
                name=f"{emoji} {tc.name}",
                as_type=as_type,
                input=tc.arguments,
            ) as tsp:
                if tc.name not in impls:
                    _log.warning("model called unknown tool: %s", tc.name)
                    output = f"unknown tool: {tc.name}"
                    if tsp is not None:
                        tsp.update(output=output, level="WARNING")
                else:
                    try:
                        output = impls[tc.name](**tc.arguments)
                        if inspect.isawaitable(output):
                            output = await output
                        if tsp is not None:
                            tsp.update(output=str(output))
                    except Exception as exc:
                        _log.warning("tool %s raised: %s", tc.name, exc)
                        output = f"tool error: {exc}"
                        if tsp is not None:
                            tsp.update(output=output, level="ERROR", status_message=str(exc))

            messages.append(
                Message(
                    role="tool",
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=str(output),
                )
            )
            if tc.name in final:
                hit_final = True

        if hit_final:
            _log.debug("loop ended: final tool reached (turn %d)", turn + 1)
            return messages

    _log.warning(
        "loop ended: max_turns=%d reached without a final tool — transcript may be incomplete",
        max_turns,
    )
    return messages


def _short(d) -> str:
    """Render a tool-input dict as a short, readable string (≤ 120 chars)."""
    try:
        items = ", ".join(f"{k}={str(v)[:40]}" for k, v in d.items())
    except Exception:
        items = ""
    return items[:120]
