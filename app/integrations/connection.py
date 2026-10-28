"""Explicit, preview-only connection recipes shared by CLI and human console."""

import copy
import json
import re
import shlex
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.integrations import DEFAULT_INTEGRATION_REGISTRY
from app.integrations.base import contains_literal_agent_context_token
from app.models.common import Identifier
from app.security.scopes import SCOPE_SET

READ_SCOPES = frozenset(s for s in SCOPE_SET if s.endswith(":read")) | {"agent:self"}
STAGES = ("configuration", "transport", "authentication", "capabilities", "project", "retrieval")


class ConnectionSelection(BaseModel):
    """No credential, implicit project, destination, or policy mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    integration_id: str = Field(min_length=1, max_length=32)
    origin: str = Field(min_length=1, max_length=2048)
    project_id: Identifier
    require_read_only: bool = False

    @field_validator("integration_id")
    @classmethod
    def known_integration(cls, value: str) -> str:
        DEFAULT_INTEGRATION_REGISTRY.get(value)
        return value

    @field_validator("origin")
    @classmethod
    def exact_origin(cls, value: str) -> str:
        # Never normalize a credential, control character, path or query away.
        if not value.isascii() or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("select a plain HTTP(S) origin")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or "\\" in value
            or parsed.port == 0
            or parsed.netloc.endswith(":")
        ):
            raise ValueError("select an exact HTTP(S) origin without credentials or path")
        try:
            ip_address(parsed.hostname)
        except ValueError:
            if not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                for part in parsed.hostname.split(".")
            ):
                raise ValueError("select a valid origin hostname") from None
        if parsed.scheme == "http":
            try:
                loopback = ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = parsed.hostname == "localhost"
            if not loopback:
                raise ValueError("remote connections require HTTPS")
        return parsed.geturl().rstrip("/")

    @model_validator(mode="after")
    def no_pasted_credential(self) -> "ConnectionSelection":
        if contains_literal_agent_context_token([self.origin, self.project_id]):
            raise ValueError("connection selection cannot contain a credential")
        return self


class ProbeSelection(ConnectionSelection):
    """Writing requires two explicit, matching selections; never implied by doctor."""

    write_probe: bool = False
    disposable_project_id: Identifier | None = None

    @model_validator(mode="after")
    def explicit_disposable(self) -> "ProbeSelection":
        if self.write_probe != (self.disposable_project_id is not None):
            raise ValueError("write probe requires an explicit disposable project")
        if self.write_probe and self.disposable_project_id != self.project_id:
            raise ValueError("disposable project must match the selected project")
        return self


def read_only_scopes(scopes: list[str] | tuple[str, ...]) -> bool:
    """Conservative server-grant classification; unknown actions never pass."""
    return bool(scopes) and set(scopes) <= READ_SCOPES


def preview(selection: ConnectionSelection) -> dict[str, object]:
    """Only generated references. Never read client config, credentials or environment."""
    integration = DEFAULT_INTEGRATION_REGISTRY.get(selection.integration_id)
    config: dict[str, Any] = copy.deepcopy(dict(integration.instructions().configuration))
    if integration.metadata.transport == "stdio":
        server = config["mcp_servers"]["agent-context"]
        server["env"]["MCP_API_BASE_URL"] = selection.origin
        server["env"]["MCP_API_CA_FILE"] = "/recipient/path/to/reviewed-ca.pem"
    elif selection.integration_id == "custom":
        config["url"] = selection.origin + "/mcp"
    else:
        server = config["mcpServers"]["agent-context"]
        server["httpUrl" if selection.integration_id == "gemini-cli" else "url"] = (
            selection.origin + "/mcp"
        )
    if integration.metadata.transport == "stdio":
        # Render the actual Codex file syntax, not JSON masquerading as TOML.
        server = config["mcp_servers"]["agent-context"]
        lines = ["[mcp_servers.agent-context]"]
        lines += [f"{k} = {json.dumps(v)}" for k, v in server.items() if k != "env"]
        lines += ["", "[mcp_servers.agent-context.env]"]
        lines += [f"{k} = {json.dumps(v)}" for k, v in server["env"].items()]
        text = "\n".join(lines) + "\n"
    else:
        text = json.dumps(config, indent=2) + "\n"
    command = shlex.join(
        [
            "contextctl",
            "integration",
            "doctor",
            selection.integration_id,
            "--origin",
            selection.origin,
            "--project-id",
            selection.project_id,
            "--token-file",
            "/recipient/path/to/platform-token",
            "--ca-file",
            "/recipient/path/to/reviewed-ca.pem",
            "--connect",
            *(["--require-read-only"] if selection.require_read_only else []),
        ]
    )
    return {
        "integration": integration.metadata.as_dict(),
        "configuration": config,
        "configuration_format": "toml" if integration.metadata.transport == "stdio" else "json",
        "configuration_text": text,
        "doctor_command": command,
        "stages": list(STAGES),
        "preview_only": True,
        "configuration_applied": False,
        "policy_changed": False,
        "server_read_only_verified": False,
        "require_read_only": selection.require_read_only,
        "next_steps": [
            "Review the selected origin and explicit project; merge only this server manually.",
            "Use the verified recipient's one-time credential, bounded by approved project grants.",
            "Inspect the evaluation CA fingerprint; use scoped trust, never disable TLS checks.",
            "For a publicly trusted certificate omit the CA file; stdio can omit MCP_API_CA_FILE.",
            "For remote Node clients configure reviewed CA trust in that client process.",
            "Replace placeholder paths locally. Keep token values out of client configuration.",
            "Run doctor with --connect from the recipient terminal; no model provider is used.",
            "Read-only requires a server-issued credential without write/approval/manage scopes.",
            "Local tool filters do not prove server policy. This preview changes no grants.",
            (
                "A lost token requires explicit revocation/replacement and "
                "new recipient-only delivery."
            ),
        ],
    }
