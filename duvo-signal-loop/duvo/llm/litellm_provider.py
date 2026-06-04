"""Default LLMProvider, backed by LiteLLM.

This is the ONLY module that imports ``litellm`` or knows the OpenAI/LiteLLM
wire format. It translates the neutral types in ``duvo.llm.base`` to LiteLLM's
``acompletion`` call and back.
"""

import json
from typing import Any

import litellm

from duvo import config
from duvo.infra.logging_setup import get_logger
from duvo.llm.base import LLMRequest, LLMResponse, Message, ToolCall, ToolSpec
from duvo.llm.registry import register_llm

_log = get_logger(__name__)

# LiteLLM is chatty on import/first call; keep our logs clean.
litellm.suppress_debug_info = True


def _to_wire_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _to_wire_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    wire: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            wire.append(
                {
                    "role": "assistant",
                    "content": m.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        elif m.role == "tool":
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "name": m.name or "",
                    "content": m.content,
                }
            )
        else:
            wire.append({"role": m.role, "content": m.content})
    return wire


def _parse_arguments(raw: str | None, tool_name: str) -> dict[str, Any]:
    """Parse a tool-call ``arguments`` JSON string defensively (never raises)."""
    try:
        args = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        _log.warning("could not parse tool arguments for %s — using {}", tool_name)
        return {}
    return args if isinstance(args, dict) else {}


def _usage_from_wire(resp: Any) -> dict[str, int] | None:
    """Map a litellm usage object to neutral {"input","output","total"} (None if absent)."""
    u = getattr(resp, "usage", None)
    if u is None:
        return None
    out: dict[str, int] = {}
    pt = getattr(u, "prompt_tokens", None)
    ct = getattr(u, "completion_tokens", None)
    tt = getattr(u, "total_tokens", None)
    if isinstance(pt, int):
        out["input"] = pt
    if isinstance(ct, int):
        out["output"] = ct
    if isinstance(tt, int):
        out["total"] = tt
    return out or None


def _cost_from_wire(resp: Any) -> float | None:
    """Return the call's USD cost from litellm (None if it can't be determined)."""
    hidden = getattr(resp, "_hidden_params", None)
    if isinstance(hidden, dict):
        cost = hidden.get("response_cost")
        if cost is not None:
            try:
                return float(cost)
            except (TypeError, ValueError):
                pass
    try:
        return float(litellm.completion_cost(completion_response=resp))
    except Exception:
        return None


def _from_wire_response(resp: Any) -> LLMResponse:
    choice = resp.choices[0]
    msg = choice.message
    tool_calls: list[ToolCall] = []
    for tc in msg.tool_calls or []:
        tool_calls.append(
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=_parse_arguments(tc.function.arguments, tc.function.name),
            )
        )
    assistant = Message(role="assistant", content=msg.content or "", tool_calls=tool_calls)
    return LLMResponse(
        message=assistant,
        tool_calls=tool_calls,
        stop_reason=choice.finish_reason or "",
        usage=_usage_from_wire(resp),
        cost_usd=_cost_from_wire(resp),
    )


@register_llm("litellm")
class LiteLLMProvider:
    """Calls any LiteLLM-supported model via the neutral :class:`LLMProvider` contract."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        tools = [_to_wire_tool(t) for t in request.tools] or None
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": _to_wire_messages(request.system, request.messages),
            "max_tokens": request.max_tokens,
            "num_retries": config.LLM_MAX_RETRIES,
            "timeout": config.ANTHROPIC_TIMEOUT_SECONDS,
            "drop_params": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        # Hosted providers read their key from the environment (loaded via dotenv in
        # config). For a custom OpenAI-compatible endpoint (vLLM/Ollama) we set the
        # base URL explicitly, and the OpenAI client requires *some* api_key string.
        if config.LLM_BASE_URL:
            kwargs["api_base"] = config.LLM_BASE_URL
            kwargs["api_key"] = config.OPENAI_API_KEY or "sk-no-key-required"
        resp = await litellm.acompletion(**kwargs)
        return _from_wire_response(resp)
