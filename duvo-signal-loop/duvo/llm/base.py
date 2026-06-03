"""Provider-neutral LLM contract — the types every agent and provider speaks.

Wire formats (OpenAI / Anthropic / LiteLLM) never appear here or anywhere
outside ``duvo/llm/<provider>_provider.py``. ``agent_core`` and the agents use
only these neutral types, so swapping the underlying provider changes no
consumer code.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class ToolCall:
    """A single tool invocation requested by the model.

    Args:
        id:        Provider-assigned id, echoed back when returning the result.
        name:      Tool name (must match a key in the agent's ``impls`` dict).
        arguments: Parsed JSON arguments as a dict (already ``json.loads``-ed).
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """One turn in the conversation, in neutral form.

    Args:
        role:         "system" | "user" | "assistant" | "tool".
        content:      Text content (empty for an assistant turn that only calls tools).
        tool_calls:   For an assistant turn: the tools it requested (else empty).
        tool_call_id: For a "tool" result turn: the id of the call it answers.
        name:         For a "tool" result turn: the tool name.
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class ToolSpec:
    """A tool definition in neutral form (JSON-Schema parameters)."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class LLMRequest:
    """A single model call."""

    model: str
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    max_tokens: int = 2000


@dataclass
class LLMResponse:
    """A single model response.

    ``message`` is the assistant turn to append to the transcript (it carries
    any ``tool_calls`` so the provider can serialize them back correctly).
    ``tool_calls`` is the same list, surfaced for the agent loop's convenience.
    """

    message: Message
    tool_calls: list[ToolCall]
    stop_reason: str


def tool_schema(name: str, description: str, parameters: dict[str, Any]) -> ToolSpec:
    """Build a :class:`ToolSpec`. Agents use this instead of hand-writing wire format.

    Args:
        name:        Tool name (matches the ``impls`` key and ``final_tools`` entry).
        description: Human/model-facing description.
        parameters:  JSON-Schema object describing the tool's input.

    Returns:
        A :class:`ToolSpec` the provider translates to its wire format.
    """
    return ToolSpec(name=name, description=description, parameters=parameters)


class LLMProvider(Protocol):
    """The swap point: one async method that runs a single model turn.

    Implementations live in ``duvo/llm/<name>_provider.py`` and register
    themselves via :func:`duvo.llm.registry.register_llm`.
    """

    def complete(self, request: LLMRequest) -> Awaitable[LLMResponse]:
        """Run one model call and return the normalized response."""
        ...
