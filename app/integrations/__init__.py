"""Safe, client-facing configuration and onboarding metadata.

The registry describes clients that all use the same MCP gateway.  It does not
contain a client-specific knowledge backend or an alternate authorization path.
"""

from app.integrations.base import (
    ConnectionCheck,
    ConnectionValidation,
    IntegrationInstructions,
    IntegrationMetadata,
    IntegrationRegistry,
    McpClientIntegration,
    UnknownIntegrationError,
)
from app.integrations.claude_code import INTEGRATION as CLAUDE_CODE_INTEGRATION
from app.integrations.codex import INTEGRATION as CODEX_INTEGRATION
from app.integrations.custom import INTEGRATION as CUSTOM_INTEGRATION
from app.integrations.gemini_cli import INTEGRATION as GEMINI_CLI_INTEGRATION

DEFAULT_INTEGRATION_REGISTRY = IntegrationRegistry(
    (
        CODEX_INTEGRATION,
        CLAUDE_CODE_INTEGRATION,
        GEMINI_CLI_INTEGRATION,
        CUSTOM_INTEGRATION,
    )
)

__all__ = [
    "CLAUDE_CODE_INTEGRATION",
    "CODEX_INTEGRATION",
    "CUSTOM_INTEGRATION",
    "DEFAULT_INTEGRATION_REGISTRY",
    "GEMINI_CLI_INTEGRATION",
    "ConnectionCheck",
    "ConnectionValidation",
    "IntegrationInstructions",
    "IntegrationMetadata",
    "IntegrationRegistry",
    "McpClientIntegration",
    "UnknownIntegrationError",
]
