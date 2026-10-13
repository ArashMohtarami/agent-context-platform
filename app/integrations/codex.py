"""Codex configuration metadata for the existing local stdio MCP bridge.

Codex keeps its established V1 stdio launcher.  This adapter only describes
that configuration so the generic onboarding workflow can list it alongside
remote MCP clients; it does not alter Codex, its launcher, or gateway policy.
"""

from collections.abc import Mapping

from app.integrations.base import (
    SERVER_NAME,
    ConnectionValidation,
    IntegrationInstructions,
    IntegrationMetadata,
    McpClientIntegration,
    stdio_mcp_diagnostics,
)
from app.models.agent import AgentClientType

CODEX_INTEGRATION_ID = "codex"
MCP_API_BASE_URL_ENV = "MCP_API_BASE_URL"
MCP_API_TOKEN_FILE_ENV = "MCP_API_TOKEN_FILE"  # noqa: S105 - an environment-variable name.

METADATA = IntegrationMetadata(
    integration_id=CODEX_INTEGRATION_ID,
    display_name="Codex",
    client_type=AgentClientType.CODEX,
    transport="stdio",
    configuration_locations=(".codex/config.toml", "~/.codex/config.toml"),
    documentation_path="docs/codex.md",
    compatibility=(
        "Uses the existing stdio launcher and the shared capability-based MCP tools.",
        "The launcher reaches the Context API; it does not receive MongoDB credentials.",
        "Credential and project grants remain enforced by the existing gateway/API path.",
    ),
)


def configuration() -> dict[str, object]:
    """Return a commit-safe example without a token or filesystem secret value."""
    return {
        "mcp_servers": {
            SERVER_NAME: {
                "command": "/absolute/path/to/agent-context-platform/scripts/codex-mcp-server.sh",
                "env": {
                    MCP_API_BASE_URL_ENV: "http://127.0.0.1:8000",
                    MCP_API_TOKEN_FILE_ENV: "/operator-managed/path/agent-context-token",
                    "LOG_LEVEL": "INFO",
                },
                "startup_timeout_sec": 30,
                "tool_timeout_sec": 60,
                "required": True,
            }
        }
    }


def instructions() -> IntegrationInstructions:
    """Generate safe Codex setup and verification guidance."""
    return IntegrationInstructions(
        metadata=METADATA,
        environment_variables=(MCP_API_BASE_URL_ENV, MCP_API_TOKEN_FILE_ENV),
        configuration=configuration(),
        commands=(
            "codex mcp list",
            f"codex mcp get {SERVER_NAME}",
        ),
        verification=(
            "Start Codex from a trusted project and inspect /mcp.",
            "Call get_platform_capabilities, then search_knowledge for a known term.",
            "Use an authorized project_id before saving context or memory.",
        ),
        safety_notes=(
            "Keep the API credential in an operator-managed file; never commit it in config.",
            "The launcher has no shell, MongoDB, or unrestricted filesystem MCP tool.",
        ),
    )


def connection_diagnostics(values: Mapping[str, str] | None = None) -> ConnectionValidation:
    """Validate local stdio configuration without reading its token file."""
    return stdio_mcp_diagnostics(CODEX_INTEGRATION_ID, values)


INTEGRATION = McpClientIntegration(
    metadata=METADATA,
    instruction_factory=instructions,
    diagnostic_factory=connection_diagnostics,
)


__all__ = [
    "CODEX_INTEGRATION_ID",
    "INTEGRATION",
    "MCP_API_BASE_URL_ENV",
    "MCP_API_TOKEN_FILE_ENV",
    "METADATA",
    "configuration",
    "connection_diagnostics",
    "instructions",
]
