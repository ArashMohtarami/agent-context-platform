"""A conservative onboarding template for future MCP-compatible clients."""

from collections.abc import Mapping

from app.integrations.base import (
    MCP_URL_ENV,
    SERVER_NAME,
    TOKEN_ENV,
    ConnectionValidation,
    IntegrationInstructions,
    IntegrationMetadata,
    McpClientIntegration,
    remote_mcp_diagnostics,
)
from app.models.agent import AgentClientType

CUSTOM_INTEGRATION_ID = "custom"

METADATA = IntegrationMetadata(
    integration_id=CUSTOM_INTEGRATION_ID,
    display_name="Custom MCP Client",
    client_type=AgentClientType.CUSTOM,
    transport="streamable-http",
    configuration_locations=("client-managed configuration",),
    documentation_path="docs/client-onboarding.md",
    compatibility=(
        "Use the existing Streamable HTTP /mcp endpoint and bearer header.",
        "Implement MCP initialize, tools/list, and tools/call before production use.",
        (
            "Client-side tool selection is convenience only; gateway scopes and projects are "
            "authoritative."
        ),
    ),
)


def configuration() -> dict[str, object]:
    """Return transport-neutral JSON-shaped remote MCP settings with references only."""
    return {
        "server_name": SERVER_NAME,
        "transport": "streamable-http",
        "url": f"${{{MCP_URL_ENV}}}",
        "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"},
    }


def instructions() -> IntegrationInstructions:
    """Generate instructions that do not assume a vendor-specific config format."""
    return IntegrationInstructions(
        metadata=METADATA,
        environment_variables=(MCP_URL_ENV, TOKEN_ENV),
        configuration=configuration(),
        commands=(),
        verification=(
            "Connect to /mcp using Streamable HTTP and initialize an MCP session.",
            "Discover tools, call get_platform_capabilities, and perform an authorized read.",
            "Verify one revision-guarded write using a dedicated test project and credential.",
        ),
        safety_notes=(
            "Use one independently issued Agent Context credential per client installation.",
            "Use TLS for every remote deployment and do not put a token in a URL or config file.",
            "Do not treat tool filters, client metadata, or trust settings as authorization.",
        ),
    )


def connection_diagnostics(values: Mapping[str, str] | None = None) -> ConnectionValidation:
    """Run the common local remote-MCP preflight with no outbound connection."""
    return remote_mcp_diagnostics(CUSTOM_INTEGRATION_ID, values)


INTEGRATION = McpClientIntegration(
    metadata=METADATA,
    instruction_factory=instructions,
    diagnostic_factory=connection_diagnostics,
)


__all__ = [
    "CUSTOM_INTEGRATION_ID",
    "INTEGRATION",
    "METADATA",
    "configuration",
    "connection_diagnostics",
    "instructions",
]
