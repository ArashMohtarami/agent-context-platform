"""Capability discovery for every MCP client using the shared gateway."""

from typing import Any

from app import __version__
from app.mcp.client import ContextApiClient
from app.mcp.contract import current_call_contract
from app.mcp.schemas import (
    DEFAULT_PAGE_LIMIT,
    MAX_RETRIEVAL_LIMIT,
    GetPlatformCapabilitiesArguments,
)
from app.mcp.tools.base import ToolDefinition, ToolResult, tool

_VERSIONS = {
    "platform": __version__,
    "knowledge": "1",
    "creator": "2",
    "research": "3",
    "gateway": "4",
}
_MODULES = ("knowledge", "creator", "research", "gateway")
_CAPABILITY_GROUPS = (
    {
        "name": "knowledge",
        "capabilities": ("skills", "projects", "project_context", "memory", "search"),
    },
    {
        "name": "creator",
        "capabilities": ("creator_profiles", "content"),
    },
    {
        "name": "research",
        "capabilities": (
            "research_projects",
            "sources",
            "evidence",
            "claims",
            "knowledge_candidates",
        ),
    },
    {
        "name": "gateway",
        "capabilities": (
            "agent_identity",
            "project_resolution",
            "activity_audit",
            "agent_sessions",
        ),
    },
)


async def get_platform_capabilities(
    client: ContextApiClient,
    arguments: GetPlatformCapabilitiesArguments,
) -> ToolResult:
    """Return safe, effective capability facts for the authenticated caller."""
    _ = client, arguments
    contract = current_call_contract()
    grant = {} if contract is None else contract.authorization.describe()
    scopes = grant.get("scopes")
    projects = grant.get("projects")
    scope_names = (
        [scope for scope in scopes if isinstance(scope, str)] if isinstance(scopes, list) else []
    )
    project_access = _project_access(projects)
    data: dict[str, Any] = {
        "versions": dict(_VERSIONS),
        "enabled_modules": list(_MODULES),
        "capability_groups": [
            {"name": group["name"], "capabilities": list(group["capabilities"])}
            for group in _CAPABILITY_GROUPS
        ],
        "caller": {
            "scopes": scope_names,
            "project_access": project_access,
        },
        "limits": {
            "page_limit_default": DEFAULT_PAGE_LIMIT,
            "page_limit_max": 50,
            "retrieval_limit_max": MAX_RETRIEVAL_LIMIT,
            "query_max_characters": 500,
            "scope_filter_max": 4,
        },
    }
    return ToolResult(_render(data), data)


def _project_access(projects: object) -> dict[str, object]:
    """Present a project grant without leaking projects outside the grant."""
    if projects == "all":
        return {"mode": "all"}
    if isinstance(projects, list) and all(isinstance(project_id, str) for project_id in projects):
        if not projects:
            return {"mode": "none", "project_ids": []}
        return {"mode": "allowlist", "project_ids": list(projects)}
    return {"mode": "none", "project_ids": []}


def _render(data: dict[str, Any]) -> str:
    """Give model clients a compact orientation without replacing structured data."""
    modules = ", ".join(str(module) for module in data["enabled_modules"])
    scopes = data["caller"]["scopes"]
    return "\n".join(
        (
            "## Platform capabilities",
            "",
            f"Enabled modules: {modules}",
            f"Granted scopes: {', '.join(scopes) if scopes else 'none'}",
            "Use the discovered client-neutral tools; the gateway enforces these scopes and "
            "project access.",
        )
    )


def definitions() -> tuple[ToolDefinition, ...]:
    """Return the one client-neutral discovery tool."""
    return (
        tool(
            "get_platform_capabilities",
            title="Get platform capabilities",
            description=(
                "Report the platform modules, stable capability groups, effective caller scopes, "
                "project-access summary, and public MCP limits. It never returns credentials, "
                "network details, or database configuration."
            ),
            scope="agent:self",
            arguments=GetPlatformCapabilitiesArguments,
            handler=get_platform_capabilities,
            stable_envelope=True,
        ),
    )
