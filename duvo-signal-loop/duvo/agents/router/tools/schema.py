"""Shared schema helper for router tools."""

from duvo.llm.base import ToolSpec, tool_schema


def router_tool_schema(name: str, desc: str) -> ToolSpec:
    """Build a no-parameter ToolSpec for a router write-back tool."""
    return tool_schema(name, desc, {"type": "object", "properties": {}})
