"""Small, client-owned session lifecycle tools for the shared MCP gateway."""

from typing import Any

from app.mcp.client import ContextApiClient
from app.mcp.schemas import (
    BeginAgentSessionArguments,
    EndAgentSessionArguments,
    GetAgentSessionArguments,
    UpdateAgentSessionArguments,
)
from app.mcp.tools.base import ToolDefinition, ToolResult, tool


def _session_result(body: dict[str, Any], *, action: str) -> ToolResult:
    """Return only a small lifecycle record, never workspace conversation data."""
    session_id = body.get("session_id")
    return ToolResult(
        f"{action} agent session `{session_id}`.",
        {"session": body},
    )


async def begin_agent_session(
    client: ContextApiClient, arguments: BeginAgentSessionArguments
) -> ToolResult:
    """Create an authenticated-client session and attach it to later calls."""
    body = await client.request(
        "POST",
        "/api/v4/gateway/sessions",
        json=arguments.model_dump(exclude_none=True),
    )
    return _session_result(body, action="Started")


async def get_agent_session(
    client: ContextApiClient, arguments: GetAgentSessionArguments
) -> ToolResult:
    """Read the caller's current or historical lightweight session."""
    body = await client.request("GET", f"/api/v4/gateway/sessions/{arguments.session_id}")
    return _session_result(body, action="Loaded")


async def update_agent_session(
    client: ContextApiClient, arguments: UpdateAgentSessionArguments
) -> ToolResult:
    """Apply one revision-guarded metadata or project association update."""
    changes = arguments.model_dump(
        exclude={"session_id"},
        exclude_unset=True,
    )
    body = await client.request(
        "PATCH",
        f"/api/v4/gateway/sessions/{arguments.session_id}",
        json=changes,
    )
    return _session_result(body, action="Updated")


async def end_agent_session(
    client: ContextApiClient, arguments: EndAgentSessionArguments
) -> ToolResult:
    """Close the session after its final correlated tool activity is recorded."""
    body = await client.request(
        "POST",
        f"/api/v4/gateway/sessions/{arguments.session_id}/end",
        json={"expected_revision": arguments.expected_revision},
    )
    return _session_result(body, action="Ended")


def definitions() -> tuple[ToolDefinition, ...]:
    """Return client-neutral session tools for both Codex and Claude Code."""
    return (
        tool(
            "begin_agent_session",
            title="Begin agent session",
            description=(
                "Start a lightweight client-owned session for project association and "
                "activity correlation. It stores only small operational metadata, never "
                "conversation messages, prompts, or model context."
            ),
            scope="session:write",
            arguments=BeginAgentSessionArguments,
            handler=begin_agent_session,
            writes=True,
            stable_envelope=True,
            session_lifecycle="begin",
        ),
        tool(
            "get_agent_session",
            title="Get agent session",
            description="Read one of this client's lightweight lifecycle sessions.",
            scope="agent:self",
            arguments=GetAgentSessionArguments,
            handler=get_agent_session,
            stable_envelope=True,
        ),
        tool(
            "update_agent_session",
            title="Update agent session",
            description=(
                "Update project association, repository, or small operational metadata. "
                "expected_revision is required; reload and compare after a conflict."
            ),
            scope="session:write",
            arguments=UpdateAgentSessionArguments,
            handler=update_agent_session,
            writes=True,
            stable_envelope=True,
        ),
        tool(
            "end_agent_session",
            title="End agent session",
            description="End an active session using its current revision.",
            scope="session:write",
            arguments=EndAgentSessionArguments,
            handler=end_agent_session,
            writes=True,
            stable_envelope=True,
            session_lifecycle="end",
        ),
    )
