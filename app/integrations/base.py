"""Client-neutral MCP integration metadata and safe onboarding primitives.

This package describes how a client reaches the existing gateway.  It never
creates credentials, talks to MongoDB, or implements a knowledge capability;
those remain the responsibility of the V4 administration services and the
single MCP gateway.
"""

import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

from app.models.agent import AgentClientType
from app.security.keys import MalformedKeyError, parse

MCP_URL_ENV = "AGENT_CONTEXT_MCP_URL"
TOKEN_ENV = "AGENT_CONTEXT_TOKEN"  # noqa: S105 - an environment-variable name, not a secret.
SERVER_NAME = "agent-context"
_AGENT_CONTEXT_TOKEN = re.compile(r"\bacp_[0-9a-f]{16}_[A-Za-z0-9_-]{32,}\b")


@dataclass(frozen=True, slots=True)
class IntegrationMetadata:
    """Public facts about one supported MCP client configuration shape."""

    integration_id: str
    display_name: str
    client_type: AgentClientType
    transport: str
    configuration_locations: tuple[str, ...]
    documentation_path: str
    compatibility: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """Return JSON-ready public metadata without configuration secrets."""
        return {
            "integration_id": self.integration_id,
            "display_name": self.display_name,
            "client_type": self.client_type.value,
            "transport": self.transport,
            "configuration_locations": list(self.configuration_locations),
            "documentation_path": self.documentation_path,
            "compatibility": list(self.compatibility),
        }


@dataclass(frozen=True, slots=True)
class IntegrationInstructions:
    """Token-free setup material suitable for CLI and documentation output."""

    metadata: IntegrationMetadata
    environment_variables: tuple[str, ...]
    configuration: Mapping[str, object]
    commands: tuple[str, ...]
    verification: tuple[str, ...]
    safety_notes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """Return only presentation-safe integration instructions."""
        return {
            "integration": self.metadata.as_dict(),
            "environment_variables": list(self.environment_variables),
            "configuration": dict(self.configuration),
            "commands": list(self.commands),
            "verification": list(self.verification),
            "safety_notes": list(self.safety_notes),
        }


@dataclass(frozen=True, slots=True)
class ConnectionCheck:
    """One secret-free local preflight result."""

    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ConnectionValidation:
    """Configuration-only diagnostics; no connection or model call is made."""

    integration_id: str
    ready: bool
    checks: tuple[ConnectionCheck, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "integration_id": self.integration_id,
            "ready": self.ready,
            "network_checked": False,
            "checks": [check.as_dict() for check in self.checks],
        }


InstructionFactory = Callable[[], IntegrationInstructions]
DiagnosticFactory = Callable[[Mapping[str, str] | None], ConnectionValidation]


@dataclass(frozen=True, slots=True)
class McpClientIntegration:
    """An adapter with client-facing metadata only, never business behavior."""

    metadata: IntegrationMetadata
    instruction_factory: InstructionFactory
    diagnostic_factory: DiagnosticFactory

    def instructions(self) -> IntegrationInstructions:
        """Generate a fresh token-free instruction payload."""
        return self.instruction_factory()

    def validate(self, values: Mapping[str, str] | None = None) -> ConnectionValidation:
        """Run local configuration checks without making a network request."""
        return self.diagnostic_factory(values)


class UnknownIntegrationError(ValueError):
    """A caller named an integration the registry does not support."""


class IntegrationRegistry:
    """Resolve static client adapters without branching gateway behavior by client."""

    def __init__(self, integrations: Sequence[McpClientIntegration]) -> None:
        records = {item.metadata.integration_id: item for item in integrations}
        if len(records) != len(integrations):
            raise ValueError("integration identifiers must be unique")
        self._records = records

    def list(self) -> tuple[IntegrationMetadata, ...]:
        """Return metadata in stable operator-facing order."""
        return tuple(self._records[name].metadata for name in sorted(self._records))

    def get(self, integration_id: str) -> McpClientIntegration:
        """Return one configured integration or a safe validation error."""
        try:
            return self._records[integration_id]
        except KeyError as error:
            raise UnknownIntegrationError("unknown MCP integration") from error


def contains_literal_agent_context_token(value: object) -> bool:
    """Detect a pasted ACP token without returning or retaining that token."""
    if isinstance(value, str):
        return _AGENT_CONTEXT_TOKEN.search(value) is not None
    if isinstance(value, Mapping):
        return any(contains_literal_agent_context_token(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(contains_literal_agent_context_token(item) for item in value)
    return False


def remote_mcp_diagnostics(
    integration_id: str,
    values: Mapping[str, str] | None = None,
) -> ConnectionValidation:
    """Validate the standard remote MCP environment without exposing values.

    This is intentionally a local preflight.  Calling it never invokes a
    hosted model, reaches the MCP endpoint, or places the bearer token in a
    result.  A user can follow the returned verification steps when an actual
    connection test is desired.
    """
    environment: Mapping[str, str] = os.environ if values is None else values
    mcp_url = environment.get(MCP_URL_ENV, "").strip()
    token = environment.get(TOKEN_ENV, "").strip()
    endpoint_check = _remote_endpoint_check(mcp_url)
    token_check = _token_check(token)
    checks = (endpoint_check, token_check)
    return ConnectionValidation(
        integration_id=integration_id,
        ready=all(check.status == "ok" for check in checks),
        checks=checks,
    )


def stdio_mcp_diagnostics(
    integration_id: str,
    values: Mapping[str, str] | None = None,
) -> ConnectionValidation:
    """Validate Codex's existing stdio bridge settings without reading files."""
    environment: Mapping[str, str] = os.environ if values is None else values
    api_url = environment.get("MCP_API_BASE_URL", "").strip()
    token_file = environment.get("MCP_API_TOKEN_FILE", "").strip()
    if not api_url:
        endpoint = ConnectionCheck(
            name="MCP_API_BASE_URL", status="missing", detail="set the Context API base URL"
        )
    else:
        parsed = urlsplit(api_url)
        endpoint = (
            ConnectionCheck("MCP_API_BASE_URL", "ok", "Context API URL is configured")
            if parsed.scheme in {"http", "https"} and parsed.hostname
            else ConnectionCheck(
                "MCP_API_BASE_URL", "invalid", "Context API URL must use http or https"
            )
        )
    credential_source = ConnectionCheck(
        name="MCP_API_TOKEN_FILE",
        status="configured" if token_file else "optional",
        detail=(
            "an operator-managed token file is configured"
            if token_file
            else "not configured; this is valid only when the Context API does not require a token"
        ),
    )
    return ConnectionValidation(
        integration_id=integration_id,
        ready=endpoint.status == "ok",
        checks=(endpoint, credential_source),
    )


def _remote_endpoint_check(mcp_url: str) -> ConnectionCheck:
    if not mcp_url:
        return ConnectionCheck(
            name=MCP_URL_ENV, status="missing", detail="set the full /mcp endpoint URL"
        )
    parsed = urlsplit(mcp_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ConnectionCheck(
            name=MCP_URL_ENV, status="invalid", detail="MCP endpoint must use http or https"
        )
    if parsed.username is not None or parsed.password is not None:
        return ConnectionCheck(
            name=MCP_URL_ENV, status="invalid", detail="MCP endpoint must not contain credentials"
        )
    if parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/mcp":
        return ConnectionCheck(
            name=MCP_URL_ENV, status="invalid", detail="MCP endpoint must point to /mcp"
        )
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        return ConnectionCheck(
            name=MCP_URL_ENV,
            status="invalid",
            detail="remote MCP requires HTTPS outside controlled localhost development",
        )
    return ConnectionCheck(name=MCP_URL_ENV, status="ok", detail="MCP endpoint is configured")


def _token_check(token: str) -> ConnectionCheck:
    if not token:
        return ConnectionCheck(
            name=TOKEN_ENV, status="missing", detail="set a dedicated Agent Context credential"
        )
    try:
        parse(token)
    except MalformedKeyError:
        return ConnectionCheck(
            name=TOKEN_ENV,
            status="invalid",
            detail="credential must be a dedicated Agent Context token",
        )
    return ConnectionCheck(name=TOKEN_ENV, status="ok", detail="credential has Agent Context shape")


def _is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False
