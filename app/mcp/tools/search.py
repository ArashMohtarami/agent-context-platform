"""The unified search tool: one question, every source that can answer it."""

from app.mcp.client import ContextApiClient
from app.mcp.schemas import SearchKnowledgeArguments
from app.mcp.tools.base import ToolDefinition, ToolResult, retrieval_result, tool


async def search_knowledge(
    client: ContextApiClient,
    arguments: SearchKnowledgeArguments,
) -> ToolResult:
    """Search every source this request may see, ranked together."""
    body = await client.request(
        "POST",
        "/api/v1/search",
        json={
            "query": arguments.query,
            "project_id": arguments.project_id,
            "sources": arguments.sources,
            "context_types": arguments.context_types,
            "memory_types": arguments.memory_types,
            "memory_scopes": arguments.memory_scopes,
            "tags": arguments.tags,
            "trust_level": arguments.trust_level,
            "minimum_importance": arguments.minimum_importance,
            "phrase": arguments.phrase,
            "token_budget": arguments.token_budget,
            "limit": arguments.limit,
        },
    )
    return retrieval_result(body, heading="Relevant context")


def definitions() -> tuple[ToolDefinition, ...]:
    """Return the unified search tool."""
    return (
        tool(
            "search_knowledge",
            title="Search knowledge",
            description=(
                "The main retrieval tool. Answers one question from skills, a project's own "
                "recorded context, and memory at once, ranked against each other and cut to a "
                "token budget. Name a project to reach what that system recorded about itself; "
                "omit it to search global knowledge only. Prefer this over the per-source "
                "search tools unless you specifically want one kind of knowledge."
            ),
            scope="search:read",
            arguments=SearchKnowledgeArguments,
            handler=search_knowledge,
        ),
    )
