"""Project tools: which boundaries exist, and what each one is.

Creating a project is deliberately absent. A project is the isolation boundary
every other rule is written against, so bringing one into existence is an
operator's decision made through the API, not something an agent does in the
middle of answering a question.
"""

from app.mcp.client import ContextApiClient
from app.mcp.formatting import compact_project, render_listing, render_record
from app.mcp.schemas import GetProjectArguments, ListProjectsArguments, ResolveProjectArguments
from app.mcp.tools.base import ToolDefinition, ToolResult, tool


async def list_projects(client: ContextApiClient, arguments: ListProjectsArguments) -> ToolResult:
    """List the projects this platform knows about."""
    body = await client.request(
        "GET",
        "/api/v1/projects",
        params={"limit": arguments.limit, "cursor": arguments.cursor},
    )
    items = [compact_project(item) for item in body.get("items", [])]
    data = {"projects": items, "next_cursor": body.get("next_cursor")}
    return ToolResult(render_listing(items, heading="Projects", label="name"), data)


async def get_project(client: ContextApiClient, arguments: GetProjectArguments) -> ToolResult:
    """Load one project boundary."""
    body = await client.request("GET", f"/api/v1/projects/{arguments.project_id}")
    project = compact_project(body)
    return ToolResult(
        render_record(project, heading="Project", content_field="description"),
        {"project": project},
    )


async def resolve_project(
    client: ContextApiClient, arguments: ResolveProjectArguments
) -> ToolResult:
    """Resolve only an explicit configured project identifier or binding."""
    payload = arguments.model_dump(exclude_none=True, exclude={"workspace_alias"})
    if arguments.workspace_alias is not None:
        # The V4 API contract calls this input ``workspace_hint``.  The alias
        # is accepted only at the MCP compatibility boundary; resolution stays
        # in the existing shared V4 service.
        payload["workspace_hint"] = arguments.workspace_alias
    body = await client.request(
        "POST",
        "/api/v4/project-bindings/resolve",
        json=payload,
    )
    project_id = body.get("project_id")
    matched_by = body.get("matched_by")
    candidates = body.get("candidates", [])
    if project_id is not None:
        text = f"Resolved project `{project_id}` by {matched_by}."
    elif candidates:
        candidate_ids = ", ".join(
            str(candidate.get("project_id"))
            for candidate in candidates
            if isinstance(candidate, dict) and isinstance(candidate.get("project_id"), str)
        )
        text = f"Project resolution is ambiguous; choose one of: {candidate_ids}."
    else:
        text = "No configured project matched the supplied identifiers."
    return ToolResult(
        text,
        {
            "status": body.get("status"),
            "project_id": project_id,
            "confidence": body.get("confidence"),
            "matched_by": matched_by,
            "candidates": candidates,
        },
    )


def definitions() -> tuple[ToolDefinition, ...]:
    """Return the project tools."""
    return (
        tool(
            "list_projects",
            title="List projects",
            description=(
                "List the projects this platform holds context and memory for. Use this to find "
                "the exact project_id every project-scoped tool requires."
            ),
            scope="projects:read",
            arguments=ListProjectsArguments,
            handler=list_projects,
        ),
        tool(
            "get_project",
            title="Get a project",
            description=(
                "Read one project boundary: its name, description, repository, and environment. "
                "Call this to confirm a project_id before storing anything in it."
            ),
            scope="projects:read",
            arguments=GetProjectArguments,
            handler=get_project,
        ),
        tool(
            "resolve_project",
            title="Resolve project",
            description=(
                "Map an explicit project id, Git remote, repository URL/name, or workspace "
                "alias to one configured project. Exact bindings only; ambiguous matches are "
                "returned as candidates and never selected automatically."
            ),
            scope="projects:read",
            arguments=ResolveProjectArguments,
            handler=resolve_project,
        ),
    )
