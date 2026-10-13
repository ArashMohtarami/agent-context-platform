"""The tool registry.

Every tool this server exposes is assembled here, once, so the list a client
discovers and the list the dispatcher can call are the same list by
construction. A tool that is not in this tuple does not exist.
"""

from types import MappingProxyType
from typing import Final

from app.mcp.tools import (
    capabilities,
    content,
    context,
    creator,
    memory,
    projects,
    research,
    search,
    sessions,
    skills,
    system,
)
from app.mcp.tools.base import ToolDefinition, ToolResult, retrieval_result, tool

__all__ = [
    "TOOLS",
    "TOOLS_BY_NAME",
    "ToolDefinition",
    "ToolResult",
    "retrieval_result",
    "tool",
]


def _registry() -> tuple[ToolDefinition, ...]:
    """Collect every tool, refusing a duplicate name."""
    collected: list[ToolDefinition] = []
    for module in (
        skills,
        projects,
        context,
        memory,
        search,
        system,
        creator,
        content,
        research,
        capabilities,
        sessions,
    ):
        collected.extend(module.definitions())
    names = [definition.name for definition in collected]
    if len(set(names)) != len(names):
        raise ValueError("every MCP tool must have a unique name")
    return tuple(collected)


TOOLS: Final[tuple[ToolDefinition, ...]] = _registry()
TOOLS_BY_NAME: Final[MappingProxyType[str, ToolDefinition]] = MappingProxyType(
    {definition.name: definition for definition in TOOLS}
)
