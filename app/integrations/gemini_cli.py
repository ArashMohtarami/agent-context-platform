"""Gemini CLI's current remote Streamable HTTP MCP configuration.

This module contains only client-side configuration guidance. It does not add
an MCP endpoint, tool, credential type, or authorization path: Gemini reaches
the existing authenticated ``/mcp`` gateway exactly like every remote client.
"""

import json
import os
import re
from collections.abc import Mapping, Sequence
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
from app.mcp.tools import TOOLS
from app.models.agent import AgentClientType
from app.models.client_profile import GEMINI_DEFAULT_SCOPES
from app.security.keys import MalformedKeyError, parse

MCP_URL_ENV = "AGENT_CONTEXT_MCP_URL"
TOKEN_ENV = "AGENT_CONTEXT_TOKEN"  # noqa: S105 - an environment-variable name, not a secret.
SERVER_NAME = "agent-context"
DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_DESCRIPTION = "Agent Context shared MCP gateway"
_AGENT_CONTEXT_TOKEN = re.compile(r"\bacp_[0-9a-f]{16}_[A-Za-z0-9_-]{32,}\b")
_SERVER_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")

# Keep Phase 0's recommended profile as the one default-scope authority. The
# integration must not grow a second Gemini authorization vocabulary.
GEMINI_CLI_DEFAULT_SCOPES = GEMINI_DEFAULT_SCOPES
GEMINI_CLI_RESTRICTED_SCOPES = frozenset(
    {
        "agent:manage",
        "research:approve",
        "research:promote",
        "content:publish",
    }
)
READ_ONLY_TOOLS = tuple(definition.name for definition in TOOLS if not definition.writes)
GEMINI_CLI_INTEGRATION_ID = "gemini-cli"
METADATA = IntegrationMetadata(
    integration_id=GEMINI_CLI_INTEGRATION_ID,
    display_name="Gemini CLI",
    client_type=AgentClientType.GEMINI_CLI,
    transport="streamable-http",
    configuration_locations=("~/.gemini/settings.json", ".gemini/settings.json"),
    documentation_path="docs/clients/gemini-cli.md",
    compatibility=(
        "Uses the shared Streamable HTTP /mcp endpoint and bearer authentication.",
        "Supports tool discovery, project resolution, reads, writes, and session provenance.",
        "includeTools, excludeTools, and trust are client controls, not gateway authorization.",
    ),
)


@dataclass(frozen=True, slots=True)
class GeminiCliEnvironment:
    """The two values a Gemini CLI process needs at connection time."""

    mcp_url: str
    token: str


def validate_server_alias(alias: str) -> str:
    """Validate a Gemini MCP server alias without silently changing it.

    Gemini CLI qualifies discovered tools as ``mcp_<server>_<tool>``. Its
    policy parser treats underscores in the server component ambiguously, so
    this intentionally permits hyphens but not underscores.
    """
    if not alias or alias != alias.strip() or not _SERVER_ALIAS.fullmatch(alias):
        raise ValueError("MCP server alias must use letters, digits, and hyphens only")
    return alias


def configuration(
    *,
    include_tools: Sequence[str] | None = None,
    exclude_tools: Sequence[str] | None = None,
    read_only: bool = False,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    description: str = DEFAULT_DESCRIPTION,
) -> dict[str, object]:
    """Return a token-free Streamable HTTP server configuration.

    ``includeTools`` and ``excludeTools`` are Gemini CLI discovery controls,
    not authorization. The authenticated gateway remains authoritative for
    every scope, project, approval, and revision check.
    """
    validate_server_alias(SERVER_NAME)
    normalized_include = _normalize_tool_names(include_tools, field="includeTools")
    normalized_exclude = _normalize_tool_names(exclude_tools, field="excludeTools")
    if read_only:
        if normalized_include is not None and not set(normalized_include).issubset(READ_ONLY_TOOLS):
            raise ValueError("read-only configuration can include only read-only MCP tools")
        normalized_include = READ_ONLY_TOOLS if normalized_include is None else normalized_include
    if isinstance(timeout_ms, bool) or timeout_ms <= 0:
        raise ValueError("timeout_ms must be a positive integer")
    if not description.strip():
        raise ValueError("description must not be blank")

    result: dict[str, object] = {
        "httpUrl": f"${{{MCP_URL_ENV}}}",
        "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"},
        "timeout": timeout_ms,
        "description": description,
        "trust": False,
    }
    if normalized_include is not None:
        result["includeTools"] = list(normalized_include)
    if normalized_exclude is not None:
        result["excludeTools"] = list(normalized_exclude)
    return result


def http_url_configuration() -> dict[str, object]:
    """Return :func:`configuration` under Phase 1's public helper name."""
    return configuration()


def settings_configuration(
    *,
    include_tools: Sequence[str] | None = None,
    exclude_tools: Sequence[str] | None = None,
    read_only: bool = False,
) -> dict[str, object]:
    """Return a complete, commit-safe Gemini settings fragment."""
    return {
        "mcpServers": {
            SERVER_NAME: configuration(
                include_tools=include_tools,
                exclude_tools=exclude_tools,
                read_only=read_only,
            )
        }
    }


def user_settings_configuration(*, read_only: bool = False) -> dict[str, object]:
    """Return the recommended personal ``~/.gemini/settings.json`` fragment."""
    return settings_configuration(read_only=read_only)


def project_settings_configuration(*, read_only: bool = False) -> dict[str, object]:
    """Return the recommended repository ``.gemini/settings.json`` fragment."""
    return settings_configuration(read_only=read_only)


def settings_configuration_json(*, read_only: bool = False) -> str:
    """Render the recommended settings fragment for docs and CLI output."""
    return json.dumps(settings_configuration(read_only=read_only), indent=2)


def http_url_configuration_json() -> str:
    """Render the Phase 1 compatibility helper's output safely."""
    return settings_configuration_json()


def settings_path(scope: str) -> str:
    """Return the canonical settings location for a supported configuration scope."""
    if scope == "user":
        return "~/.gemini/settings.json"
    if scope == "project":
        return ".gemini/settings.json"
    raise ValueError("scope must be 'user' or 'project'")


def mcp_add_command(*, scope: str = "user", read_only: bool = False) -> str:
    """Return the current Gemini CLI command without executing or expanding it."""
    settings_path(scope)
    parts = [
        "gemini mcp add",
        f"--scope {scope}",
        "--transport http",
        f"--timeout {DEFAULT_TIMEOUT_MS}",
        f"--description '{DEFAULT_DESCRIPTION}'",
        f"--header 'Authorization: Bearer ${{{TOKEN_ENV}}}'",
    ]
    if read_only:
        parts.append(f"--include-tools '{','.join(READ_ONLY_TOOLS)}'")
    parts.append(f"{SERVER_NAME} '${{{MCP_URL_ENV}}}'")
    return " ".join(parts)


def user_scope_command() -> str:
    """Return the user-scope form retained for Phase 1 callers."""
    return mcp_add_command()


def instructions() -> IntegrationInstructions:
    """Return safe Gemini CLI material for the generic integration registry."""
    return IntegrationInstructions(
        metadata=METADATA,
        environment_variables=(MCP_URL_ENV, TOKEN_ENV),
        configuration=settings_configuration(),
        commands=(mcp_add_command(), "gemini mcp list", "/mcp list"),
        verification=(
            "Discover agent-context tools, then call get_platform_capabilities.",
            "Search known project knowledge through search_knowledge.",
            "Use save_context only with an authorized explicit project_id.",
        ),
        safety_notes=(
            (
                "Use a dedicated Agent Context credential, never a Google or Gemini provider "
                "credential."
            ),
            "Keep trust false by default and use HTTPS for remote deployment.",
            "Client-side tool filters do not change server authorization.",
        ),
    )


def connection_diagnostics(values: Mapping[str, str] | None = None) -> ConnectionValidation:
    """Check Gemini's standard remote-MCP environment without connecting."""
    return remote_mcp_diagnostics(GEMINI_CLI_INTEGRATION_ID, values)


def _normalize_tool_names(
    names: Sequence[str] | None,
    *,
    field: str,
) -> tuple[str, ...] | None:
    """Validate explicit client-side tool filters without inferring permissions."""
    if names is None:
        return None
    if isinstance(names, str):
        raise ValueError(f"{field} must be a sequence of tool names")
    normalized = tuple(name.strip() for name in names)
    if not normalized or any(not name for name in normalized):
        raise ValueError(f"{field} must contain non-empty tool names")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} must not contain duplicate tool names")
    return normalized


def contains_literal_agent_context_token(value: object) -> bool:
    """Report a pasted Agent Context token without returning its value."""
    if isinstance(value, str):
        return _AGENT_CONTEXT_TOKEN.search(value) is not None
    if isinstance(value, Mapping):
        return any(contains_literal_agent_context_token(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(contains_literal_agent_context_token(item) for item in value)
    return False


def required_environment(values: Mapping[str, str] | None = None) -> GeminiCliEnvironment:
    """Validate a local setup without echoing its credential.

    ``AGENT_CONTEXT_MCP_URL`` is always the full endpoint URL, including
    ``/mcp``. Provider credentials are deliberately neither read nor accepted.
    """
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
    return GeminiCliEnvironment(mcp_url=mcp_url, token=token)


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
    "DEFAULT_DESCRIPTION",
    "DEFAULT_TIMEOUT_MS",
    "GEMINI_CLI_DEFAULT_SCOPES",
    "GEMINI_CLI_INTEGRATION_ID",
    "GEMINI_CLI_RESTRICTED_SCOPES",
    "INTEGRATION",
    "MCP_URL_ENV",
    "READ_ONLY_TOOLS",
    "SERVER_NAME",
    "TOKEN_ENV",
    "GeminiCliEnvironment",
    "configuration",
    "connection_diagnostics",
    "contains_literal_agent_context_token",
    "http_url_configuration",
    "http_url_configuration_json",
    "instructions",
    "mcp_add_command",
    "project_settings_configuration",
    "required_environment",
    "settings_configuration",
    "settings_configuration_json",
    "settings_path",
    "user_scope_command",
    "user_settings_configuration",
    "validate_server_alias",
]
