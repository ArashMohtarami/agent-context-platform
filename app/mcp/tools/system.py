"""The diagnostics tool: whether the platform can answer at all.

This is the one tool that must keep working while the platform does not. A
readiness probe answers 503 exactly when something is wrong, so this reads that
as an answer rather than a failure — a status tool that errors during an outage
tells a caller nothing it could not already guess.
"""

from typing import Any

from app.mcp.client import ContextApiClient
from app.mcp.schemas import SystemStatusArguments
from app.mcp.tools.base import ToolDefinition, ToolResult, tool

# Each part of readiness, named as a caller would ask about it. The full
# readiness body carries index names, statuses, and dimensions; a tool caller
# needs to know which capability is unavailable, not how it is built.
_COMPONENTS = (
    ("database", "MongoDB"),
    ("replica_set", "Replica set"),
    ("mongot", "Search coordinator"),
    ("embedding", "Embedding model"),
    ("vector_search", "Semantic search"),
    ("lexical_search", "Lexical search"),
    ("context_search", "Project context search"),
    ("memory_search", "Memory search"),
)


async def get_system_status(
    client: ContextApiClient,
    arguments: SystemStatusArguments,
) -> ToolResult:
    """Report whether the platform can serve retrieval right now."""
    _ = arguments
    status_code, body = await client.probe("/health/ready")
    ready = body.get("status") == "ready" and status_code == 200
    components = {name: _component(body.get(name)) for name, _ in _COMPONENTS}
    unavailable = sorted(name for name, healthy in components.items() if healthy is False)
    data: dict[str, Any] = {
        "ready": ready,
        "components": components,
        "unavailable": unavailable,
    }
    embedding = body.get("embedding")
    if isinstance(embedding, dict):
        data["embedding_model"] = embedding.get("model")
        data["embedding_dimensions"] = embedding.get("dimensions")
    return ToolResult(_render(ready, components, data), data)


def _component(value: object) -> bool | None:
    """Reduce one readiness entry to healthy, unhealthy, or unreported.

    Readiness reports a plain boolean for some components and a block carrying
    its own `ready` for others. Whether a component is ready is the platform's
    judgement, read as it was given; re-deriving it from the block's fields
    would put a copy of that rule here, where it would drift.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        ready = value.get("ready")
        return ready if isinstance(ready, bool) else None
    return None


def _render(ready: bool, components: dict[str, bool | None], data: dict[str, Any]) -> str:
    """Describe platform readiness in a line per capability."""
    lines = ["## Platform status", "", f"Ready: {'yes' if ready else 'no'}"]
    model = data.get("embedding_model")
    if model:
        lines.append(f"Embedding model: {model} ({data.get('embedding_dimensions')} dimensions)")
    lines.append("")
    for name, label in _COMPONENTS:
        state = components.get(name)
        lines.append(f"- {label}: {'ok' if state else 'unknown' if state is None else 'FAILING'}")
    if not ready:
        lines.extend(
            (
                "",
                "Retrieval may return nothing or fail while a capability is failing. "
                "Lexical search keeps working without the embedding model.",
            )
        )
    return "\n".join(lines)


def definitions() -> tuple[ToolDefinition, ...]:
    """Return the diagnostics tool."""
    return (
        tool(
            "get_system_status",
            title="Get system status",
            description=(
                "Report whether the platform can answer: the database, the search coordinator, "
                "the embedding model, and each search index. Call this when a search returns "
                "nothing unexpected, to tell an outage apart from an empty result."
            ),
            scope="system:read",
            arguments=SystemStatusArguments,
            handler=get_system_status,
        ),
    )
