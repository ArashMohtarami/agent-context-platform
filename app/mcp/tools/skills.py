"""Skill tools: what the curated knowledge base holds, and what it says."""

from app.mcp.client import ContextApiClient
from app.mcp.formatting import compact_skill, render_listing, render_record, render_results
from app.mcp.schemas import (
    GetSkillArguments,
    ListSkillsArguments,
    SearchSkillsArguments,
)
from app.mcp.tools.base import ToolDefinition, ToolResult, retrieval_result, tool

# Skills are retrieved through the unified search restricted to one source, so
# a skill answer is ranked by the same pipeline as every other answer.
SKILL_SOURCE = "active_skill"


async def list_skills(client: ContextApiClient, arguments: ListSkillsArguments) -> ToolResult:
    """List skill versions the platform holds."""
    body = await client.request(
        "GET",
        "/api/v1/skills",
        params={
            "status": arguments.status,
            "category": arguments.category,
            "tag": arguments.tag,
            "trust_level": arguments.trust_level,
            "limit": arguments.limit,
            "cursor": arguments.cursor,
        },
    )
    items = [compact_skill(item) for item in body.get("items", [])]
    data = {"skills": items, "next_cursor": body.get("next_cursor")}
    return ToolResult(render_listing(items, heading="Skills", label="name"), data)


async def get_skill(client: ContextApiClient, arguments: GetSkillArguments) -> ToolResult:
    """Load one skill version with its history and a page of its chunks."""
    body = await client.request(
        "GET",
        f"/api/v1/skills/{arguments.skill_id}",
        params={
            "version": arguments.version,
            "chunk_limit": arguments.chunk_limit,
            "chunk_cursor": arguments.chunk_cursor,
        },
    )
    skill = compact_skill(body.get("skill", {}))
    chunks = body.get("chunks", [])
    data = {
        "skill": skill,
        "versions": body.get("versions", []),
        "chunks": chunks,
        "next_chunk_cursor": body.get("next_chunk_cursor"),
    }
    sections = [
        {
            "title": f"{skill.get('name', arguments.skill_id)}: {chunk.get('section') or 'Body'}",
            "source": SKILL_SOURCE,
            "content": chunk.get("content", ""),
            "metadata": {"chunk_id": chunk.get("chunk_id")},
        }
        for chunk in chunks
    ]
    text = "\n\n".join(
        (
            render_record(skill, heading="Skill", content_field="description"),
            render_results(sections, heading="Skill content"),
        )
    )
    return ToolResult(text, data)


async def search_skills(client: ContextApiClient, arguments: SearchSkillsArguments) -> ToolResult:
    """Find the skill content that answers a question.

    This is the unified retrieval pipeline restricted to skills: both searches
    run, results are ranked and deduplicated, and the answer is cut to a token
    budget — the same as any other retrieval, from one source.
    """
    body = await client.request(
        "POST",
        "/api/v1/search",
        json={
            "query": arguments.query,
            "project_id": arguments.project_id,
            "sources": [SKILL_SOURCE],
            "category": arguments.category,
            "tags": arguments.tags,
            "trust_level": arguments.trust_level,
            "token_budget": arguments.token_budget,
            "limit": arguments.limit,
        },
    )
    return retrieval_result(body, heading="Relevant skills")


def definitions() -> tuple[ToolDefinition, ...]:
    """Return the skill tools."""
    return (
        tool(
            "list_skills",
            title="List skills",
            description=(
                "List the skills this platform holds, newest first. Returns metadata only: "
                "use get_skill to read one, or search_skills to find the part that answers a "
                "question."
            ),
            scope="skills:read",
            arguments=ListSkillsArguments,
            handler=list_skills,
        ),
        tool(
            "get_skill",
            title="Get a skill",
            description=(
                "Read one skill version: its metadata, its version history, and a page of its "
                "content. Defaults to the active version; name a version to read a superseded "
                "one."
            ),
            scope="skills:read",
            arguments=GetSkillArguments,
            handler=get_skill,
        ),
        tool(
            "search_skills",
            title="Search skills",
            description=(
                "Find the skill content that answers a question, ranked by meaning and by exact "
                "technical vocabulary together. Use this for general engineering knowledge; use "
                "search_context for what a specific project decided."
            ),
            scope="skills:read",
            arguments=SearchSkillsArguments,
            handler=search_skills,
        ),
    )
