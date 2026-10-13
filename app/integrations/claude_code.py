"""Claude Code's current remote HTTP MCP configuration shape.

The values deliberately remain environment-variable references.  This module
never reads, prints, persists, or expands an Agent Context bearer token.
"""

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

from app.integrations.base import (
    ConnectionValidation,
    IntegrationInstructions,
    IntegrationMetadata,
    McpClientIntegration,
    remote_mcp_diagnostics,
)
from app.models.agent import AgentClientType
from app.security.keys import MalformedKeyError, parse
from app.security.scopes import Scope

MCP_URL_ENV = "AGENT_CONTEXT_MCP_URL"
TOKEN_ENV = "AGENT_CONTEXT_TOKEN"  # noqa: S105 - an environment-variable name, not a secret.
SERVER_NAME = "agent-context"
_AGENT_CONTEXT_TOKEN = re.compile(r"\bacp_[0-9a-f]{16}_[A-Za-z0-9_-]{32,}\b")

# An operator must opt in to every capability outside this low-risk Claude Code
# baseline. In particular, a desktop coding client never receives approval,
# promotion, publishing, credential administration, or platform-admin power
# just because it identifies itself as Claude Code.
CLAUDE_CODE_DEFAULT_SCOPES: tuple[Scope, ...] = (
    "skills:read",
    "projects:read",
    "context:read",
    "context:write",
    "memory:read",
    "memory:write",
    "search:read",
    "research:read",
    "research:write",
    "agent:self",
    "session:write",
    "audit:self:read",
)
CLAUDE_CODE_RESTRICTED_SCOPES: frozenset[Scope] = frozenset(
    {
        "agent:manage",
        "research:approve",
        "research:promote",
        "content:publish",
    }
)

CLAUDE_CODE_INTEGRATION_ID = "claude-code"
METADATA = IntegrationMetadata(
    integration_id=CLAUDE_CODE_INTEGRATION_ID,
    display_name="Claude Code",
    client_type=AgentClientType.CLAUDE_CODE,
    transport="streamable-http",
    configuration_locations=(".mcp.json", "Claude Code user configuration"),
    documentation_path="docs/clients/claude-code.md",
    compatibility=(
        "Uses the shared remote HTTP /mcp endpoint and bearer authentication.",
        "Supports capability discovery, project resolution, reads, and revision-guarded writes.",
        "Project approval and gateway scopes remain authoritative over client configuration.",
    ),
)


@dataclass(frozen=True, slots=True)
class ClaudeCodeEnvironment:
    """The two values a real Claude Code process needs at connection time."""

    mcp_url: str
    token: str


def configuration() -> dict[str, object]:
    """Return a shareable remote HTTP MCP configuration with no secret value."""
    return {
        "type": "http",
        "url": f"${{{MCP_URL_ENV}}}",
        "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"},
    }


def project_configuration() -> dict[str, object]:
    """Return the complete, commit-safe ``.mcp.json`` object."""
    return {"mcpServers": {SERVER_NAME: configuration()}}


def project_configuration_json() -> str:
    """Render the exact JSON shape for documentation and CLI output."""
    return json.dumps(project_configuration(), indent=2)


def contains_literal_agent_context_token(value: object) -> bool:
    """Report whether a configuration-shaped value embeds a usable ACP token.

    The setup helper intentionally returns references. This small scanner gives
    documentation/configuration tests a concrete guard against replacing one
    with a pasted credential without ever returning that credential.
    """
    if isinstance(value, str):
        return _AGENT_CONTEXT_TOKEN.search(value) is not None
    if isinstance(value, Mapping):
        return any(contains_literal_agent_context_token(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(contains_literal_agent_context_token(item) for item in value)
    return False


def user_scope_command() -> str:
    """Return the current Claude Code CLI command without executing it.

    ``add-json`` preserves the literal variable references, unlike expanding a
    bearer value in a shell argument before Claude Code writes its user config.
    """
    config = json.dumps(configuration(), separators=(",", ":"))
    return f"claude mcp add-json --scope user {SERVER_NAME} '{config}'"


def instructions() -> IntegrationInstructions:
    """Return safe Claude Code material for the generic integration registry."""
    return IntegrationInstructions(
        metadata=METADATA,
        environment_variables=(MCP_URL_ENV, TOKEN_ENV),
        configuration=project_configuration(),
        commands=(user_scope_command(), "claude mcp get agent-context", "claude mcp list"),
        verification=(
            "Inspect the remote server definition, then connect from a reviewed project.",
            "Call get_platform_capabilities and search_knowledge with an authorized project.",
            "Use a harmless save_context write only with an explicit permitted project_id.",
        ),
        safety_notes=(
            (
                "Use an independently issued Agent Context credential, never an Anthropic "
                "provider key."
            ),
            (
                "Use HTTPS for remote deployments and keep configuration values as environment "
                "references."
            ),
        ),
    )


def connection_diagnostics(values: Mapping[str, str] | None = None) -> ConnectionValidation:
    """Check Claude's standard remote-MCP environment without connecting."""
    return remote_mcp_diagnostics(CLAUDE_CODE_INTEGRATION_ID, values)


def required_environment(values: Mapping[str, str] | None = None) -> ClaudeCodeEnvironment:
    """Validate the runtime environment without ever echoing its token."""
    environment: Mapping[str, str] = os.environ if values is None else values
    mcp_url = environment.get(MCP_URL_ENV, "").strip()
    token = environment.get(TOKEN_ENV, "").strip()
    missing = [name for name, value in ((MCP_URL_ENV, mcp_url), (TOKEN_ENV, token)) if not value]
    if missing:
        raise ValueError(f"missing required environment variable: {', '.join(missing)}")
    parsed = urlsplit(mcp_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{MCP_URL_ENV} must be an http or https MCP endpoint URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{MCP_URL_ENV} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{MCP_URL_ENV} must not contain a query or fragment")
    if parsed.path.rstrip("/") != "/mcp":
        raise ValueError(f"{MCP_URL_ENV} must point to the /mcp endpoint")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise ValueError(f"{MCP_URL_ENV} must use HTTPS outside controlled localhost development")
    try:
        parse(token)
    except MalformedKeyError as error:
        raise ValueError(f"{TOKEN_ENV} must be a dedicated Agent Context credential") from error
    return ClaudeCodeEnvironment(mcp_url=mcp_url, token=token)


def _is_loopback(hostname: str) -> bool:
    """Recognize only literal local hosts; never resolve arbitrary DNS here."""
    if hostname.lower() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


INTEGRATION = McpClientIntegration(
    metadata=METADATA,
    instruction_factory=instructions,
    diagnostic_factory=connection_diagnostics,
)


__all__ = [
    "CLAUDE_CODE_DEFAULT_SCOPES",
    "CLAUDE_CODE_INTEGRATION_ID",
    "CLAUDE_CODE_RESTRICTED_SCOPES",
    "INTEGRATION",
    "MCP_URL_ENV",
    "SERVER_NAME",
    "TOKEN_ENV",
    "ClaudeCodeEnvironment",
    "configuration",
    "connection_diagnostics",
    "contains_literal_agent_context_token",
    "instructions",
    "project_configuration",
    "project_configuration_json",
    "required_environment",
    "user_scope_command",
]
