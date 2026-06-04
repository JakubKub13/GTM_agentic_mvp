"""Terminal tool for the analyst agent to record its ICP assessment."""

from collections.abc import Callable
from typing import Any

from duvo.llm.base import ToolSpec, tool_schema

RECORD_ASSESSMENT_TOOL: ToolSpec = tool_schema(
    "record_assessment",
    "Record the final ICP assessment and drafted outreach. Call once when done.",
    {
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "tier": {"type": "string", "enum": ["Tier 1", "Tier 2", "Tier 3"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "why_fit": {"type": "array", "items": {"type": "string"}},
            "why_not": {"type": "array", "items": {"type": "string"}},
            "recommended_persona": {"type": "string"},
            "recommended_angle": {"type": "string"},
            "reasoning": {"type": "string"},
            "needs_human_research": {"type": "boolean"},
            "outreach": {
                "type": "object",
                "properties": {
                    "persona": {"type": "string"},
                    "subject": {"type": "string"},
                    "first_line": {
                        "type": "string",
                        "description": "Personalized opener grounded in a real signal.",
                    },
                    "body": {"type": "string"},
                },
                "required": ["persona", "subject", "first_line", "body"],
            },
        },
        "required": [
            "score",
            "tier",
            "confidence",
            "why_fit",
            "why_not",
            "recommended_persona",
            "recommended_angle",
            "reasoning",
            "needs_human_research",
            "outreach",
        ],
    },
)


def make_record_assessment_tool(captured: dict[str, Any]) -> Callable:
    """Build the analyst terminal tool implementation bound to a capture dict."""

    def record_assessment(**kw):
        captured.update(kw)
        return "recorded"

    return record_assessment
