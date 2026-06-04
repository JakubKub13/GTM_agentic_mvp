"""Terminal tool for scout agents to submit sourced signals."""

from collections.abc import Callable
from typing import Any

from duvo.agents.scouts.beats import BeatKey
from duvo.llm.base import ToolSpec, tool_schema
from duvo.models import Signal

SUBMIT_SIGNALS_TOOL: ToolSpec = tool_schema(
    "submit_signals",
    "Submit the sourced signals you found (may be empty). Call once when done.",
    {
        "type": "object",
        "properties": {
            "signals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string", "description": "1-2 sentences."},
                        "source_url": {"type": "string"},
                        "published_date": {
                            "type": "string",
                            "description": "ISO date or empty if unknown.",
                        },
                        "relevance": {
                            "type": "string",
                            "description": "Why this matters for Duvo.",
                        },
                    },
                    "required": ["title", "summary", "source_url", "relevance"],
                },
            }
        },
        "required": ["signals"],
    },
)


def make_submit_signals_tool(captured: dict[str, list[dict[str, Any]]]) -> Callable:
    """Build the scout terminal tool implementation bound to a capture dict."""

    def submit_signals(signals=None):
        # The model sometimes calls submit_signals() with no args to mean "found nothing".
        # Tolerate that (and a null) instead of raising, so the beat returns [] cleanly.
        signals = signals or []
        captured["signals"] = signals
        return f"received {len(signals)} signals"

    return submit_signals


def signals_from_payload(payload: list[dict[str, Any]], beat_key: BeatKey) -> list[Signal]:
    """Convert the submit_signals payload into Signal models for one scout beat."""
    return [
        Signal(
            signal_type=beat_key,
            title=s.get("title", "(no title)"),
            summary=s.get("summary", ""),
            source_url=s.get("source_url", ""),
            published_date=(s.get("published_date") or None),
            relevance=s.get("relevance", ""),
        )
        for s in payload
    ]
