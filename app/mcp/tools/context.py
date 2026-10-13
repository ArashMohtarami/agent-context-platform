"""Project context tools: what a specific system decided, and why.

Every tool here names its project, and none of them defaults it. A context item
that reached the wrong project would be both a leak and a lie about which
system made the decision, so the project is an argument a caller states rather
than something this layer works out.
"""

from app.mcp.client import ContextApiClient
from app.mcp.formatting import pick, render_record
from app.mcp.schemas import (
    GetContextArguments,
    SaveContextArguments,
    SearchContextArguments,
)
from app.mcp.tools.base import ToolDefinition, ToolResult, retrieval_result, tool

CONTEXT_SOURCE = "project_context"
# A stored item is echoed back with the fields a caller needs to refer to it
# again or to update it safely; the vector and the storage internals never
# leave the API in the first place.
_RECORD_FIELDS = (
    "context_id",
    "project_id",
    "type",
    "title",
    "importance",
    "tags",
    "source",
    "status",
    "external_id",
    "revision",
    "updated_at",
    "content",
)


async def search_context(client: ContextApiClient, arguments: SearchContextArguments) -> ToolResult:
    """Search one project's own recorded context."""
    body = await client.request(
        "POST",
        "/api/v1/context/search",
        json={
            "project_id": arguments.project_id,
            "query": arguments.query,
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
            "key": f"{CONTEXT_SOURCE}:{item['context']['context_id']}",
            "source": CONTEXT_SOURCE,
            "title": item["context"].get("title", ""),
            "content": item["context"].get("content", ""),
            "score": item.get("score"),
            "metadata": pick(item["context"], ("project_id", "type", "importance", "status")),
        }
        for item in body.get("results", [])
        if isinstance(item.get("context"), dict)
    ]
    return retrieval_result(
        {"query": body.get("query"), "project_id": body.get("project_id"), "results": results},
        heading=f"Context in {arguments.project_id}",
    )


async def get_context(client: ContextApiClient, arguments: GetContextArguments) -> ToolResult:
    """Load one context item from inside its own project."""
    body = await client.request(
        "GET",
        f"/api/v1/context/{arguments.context_id}",
        params={"project_id": arguments.project_id},
    )
    record = pick(body, _RECORD_FIELDS)
    return ToolResult(render_record(record, heading="Project context"), {"context": record})


async def save_context(client: ContextApiClient, arguments: SaveContextArguments) -> ToolResult:
    """Store one durable context item in a project."""
    body = await client.request(
        "POST",
        "/api/v1/context",
        json={
            "project_id": arguments.project_id,
            "type": arguments.type,
            "title": arguments.title,
            "content": arguments.content,
            "importance": arguments.importance,
            "source": arguments.source,
            "tags": arguments.tags,
            "status": arguments.status,
            "external_id": arguments.external_id,
        },
    )
    record = pick(body, _RECORD_FIELDS)
    saved = pick(record, tuple(field for field in _RECORD_FIELDS if field != "content"))
    return ToolResult(
        render_record(saved, heading="Stored project context", content_field=None),
        {"context": record},
    )


def definitions() -> tuple[ToolDefinition, ...]:
    """Return the project context tools."""
    return (
        tool(
            "search_context",
            title="Search project context",
            description=(
                "Search one project's own recorded context: its architecture decisions, "
                "conventions, constraints, and known issues. Requires project_id, and can only "
                "ever return that project's context."
            ),
            scope="context:read",
            arguments=SearchContextArguments,
            handler=search_context,
        ),
        tool(
            "get_context",
            title="Get a context item",
            description=(
                "Read one stored context item in full, addressed from inside its own project. "
                "An item belonging to another project is reported as not found."
            ),
            scope="context:read",
            arguments=GetContextArguments,
            handler=get_context,
        ),
        tool(
            "save_context",
            title="Save project context",
            description=(
                "Record something durable about one project: a decision, a convention, a "
                "constraint. Use this for what is true of this system specifically. Pass "
                "external_id to make a repeated save return what is already stored."
            ),
            scope="context:write",
            arguments=SaveContextArguments,
            handler=save_context,
            writes=True,
        ),
    )
