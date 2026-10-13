"""Memory tools: what stays useful across sessions.

Memory is written on purpose. Nothing here observes a conversation and decides
to remember it; a memory exists because a caller asked for it, which is what
`save_memory` is. Automatic extraction is a later phase with its own
safeguards, and this layer must not become a way around them.

A correction supersedes rather than overwrites: `save_memory` with `supersedes`
stores the new memory and marks the old one replaced, keeping both ends of the
link. There is no delete tool, because nothing in the platform deletes memory.
"""

from app.mcp.client import ContextApiClient
from app.mcp.formatting import pick, render_record
from app.mcp.schemas import (
    SaveMemoryArguments,
    SearchMemoryArguments,
    UpdateMemoryArguments,
)
from app.mcp.tools.base import ToolDefinition, ToolResult, retrieval_result, tool

MEMORY_SOURCE = "memory"
_RECORD_FIELDS = (
    "memory_id",
    "scope",
    "project_id",
    "client_id",
    "type",
    "summary",
    "importance",
    "confidence",
    "tags",
    "source",
    "status",
    "external_id",
    "supersedes",
    "superseded_by",
    "revision",
    "updated_at",
    "content",
)


async def search_memory(client: ContextApiClient, arguments: SearchMemoryArguments) -> ToolResult:
    """Search the memory this request is allowed to see."""
    body = await client.request(
        "POST",
        "/api/v1/memory/search",
        json={
            "query": arguments.query,
            "project_id": arguments.project_id,
            "scopes": arguments.scopes,
            "types": arguments.types,
            "tags": arguments.tags,
            "status": arguments.status,
            "minimum_importance": arguments.minimum_importance,
            "phrase": arguments.phrase,
            "limit": arguments.limit,
        },
    )
    results = [
        {
            "key": f"{MEMORY_SOURCE}:{item['memory']['memory_id']}",
            "source": MEMORY_SOURCE,
            "title": item["memory"].get("summary") or f"{item['memory'].get('type')} memory",
            "content": item["memory"].get("content", ""),
            "score": item.get("score"),
            "metadata": pick(
                item["memory"],
                (
                    "memory_id",
                    "scope",
                    "project_id",
                    "client_id",
                    "type",
                    "importance",
                    "confidence",
                    "status",
                    "revision",
                ),
            ),
        }
        for item in body.get("results", [])
        if isinstance(item.get("memory"), dict)
    ]
    return retrieval_result(
        {"query": body.get("query"), "project_id": body.get("project_id"), "results": results},
        heading="Relevant memory",
    )


async def save_memory(client: ContextApiClient, arguments: SaveMemoryArguments) -> ToolResult:
    """Store one memory, optionally replacing the one it corrects."""
    body = await client.request(
        "POST",
        "/api/v1/memory",
        json={
            "type": arguments.type,
            "content": arguments.content,
            "importance": arguments.importance,
            "confidence": arguments.confidence,
            "source": arguments.source,
            "scope": arguments.scope,
            "project_id": arguments.project_id,
            "summary": arguments.summary,
            "tags": arguments.tags,
            "status": arguments.status,
            "external_id": arguments.external_id,
            "supersedes": arguments.supersedes,
        },
    )
    return _saved(body, heading="Stored memory")


async def update_memory(client: ContextApiClient, arguments: UpdateMemoryArguments) -> ToolResult:
    """Apply a partial update to one stored memory."""
    changes = {
        "type": arguments.type,
        "content": arguments.content,
        "summary": arguments.summary,
        "importance": arguments.importance,
        "confidence": arguments.confidence,
        "tags": arguments.tags,
        "status": arguments.status,
        "expected_revision": arguments.expected_revision,
    }
    body = await client.request(
        "PATCH",
        f"/api/v1/memory/{arguments.memory_id}",
        params={"project_id": arguments.project_id, "scope": arguments.scope},
        json={key: value for key, value in changes.items() if value is not None},
    )
    return _saved(body, heading="Updated memory")


def _saved(body: dict[str, object], *, heading: str) -> ToolResult:
    """Confirm one write without echoing the content back into the prompt."""
    record = pick(body, _RECORD_FIELDS)
    confirmed = pick(record, tuple(field for field in _RECORD_FIELDS if field != "content"))
    return ToolResult(
        render_record(confirmed, heading=heading, content_field=None),
        {"memory": record},
    )


def definitions() -> tuple[ToolDefinition, ...]:
    """Return the memory tools."""
    return (
        tool(
            "search_memory",
            title="Search memory",
            description=(
                "Search what has been remembered on purpose: preferences, conventions, and "
                "corrections. Client-linked calls also include only that client's operational "
                "memory; shared global/project knowledge remains preferred for durable facts. "
                "Each result includes its revision for a guarded update_memory call."
            ),
            scope="memory:read",
            arguments=SearchMemoryArguments,
            handler=search_memory,
        ),
        tool(
            "save_memory",
            title="Save a memory",
            description=(
                "Remember something durable. Omit project_id for knowledge that applies "
                "everywhere; name one for knowledge about that system. Use agent scopes only "
                "for client operational knowledge; client ownership comes from the credential. "
                "When this corrects an "
                "existing memory, pass its id as supersedes: the old memory is marked replaced "
                "and kept, never deleted."
            ),
            scope="memory:write",
            arguments=SaveMemoryArguments,
            handler=save_memory,
            writes=True,
        ),
        tool(
            "update_memory",
            title="Update a memory",
            description=(
                "Change one stored memory in place, or archive it by setting status. Use this "
                "for a correction of wording or importance; use save_memory with supersedes "
                "when the knowledge itself changed. expected_revision is required: reload the "
                "memory, compare it, then retry or request review after a conflict."
            ),
            scope="memory:write",
            arguments=UpdateMemoryArguments,
            handler=update_memory,
            writes=True,
        ),
    )
