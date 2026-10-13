"""Claude Code setup output stays current, safe, and token-free."""

import json
from pathlib import Path

import pytest

from app.integrations.claude_code import (
    CLAUDE_CODE_DEFAULT_SCOPES,
    CLAUDE_CODE_RESTRICTED_SCOPES,
    MCP_URL_ENV,
    TOKEN_ENV,
    configuration,
    contains_literal_agent_context_token,
    project_configuration,
    required_environment,
    user_scope_command,
)
from app.security.keys import mint


def test_configuration_uses_current_remote_http_shape_and_variable_references() -> None:
    assert configuration() == {
        "type": "http",
        "url": "${AGENT_CONTEXT_MCP_URL}",
        "headers": {"Authorization": "Bearer ${AGENT_CONTEXT_TOKEN}"},
    }
    assert project_configuration()["mcpServers"]["agent-context"] == configuration()
    command = user_scope_command()
    assert command.startswith("claude mcp add-json --scope user agent-context ")
    assert "${AGENT_CONTEXT_TOKEN}" in command
    assert "--transport sse" not in command
    assert json.loads(command.split("'", maxsplit=2)[1]) == configuration()


@pytest.mark.parametrize(
    "environment",
    [{}, {MCP_URL_ENV: "https://agents.example.com/mcp"}, {TOKEN_ENV: "token"}],
)
def test_required_environment_refuses_missing_values(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="missing required environment variable"):
        required_environment(environment)


def test_required_environment_requires_an_mcp_endpoint_without_embedded_credentials() -> None:
    token = mint().token
    with pytest.raises(ValueError, match="/mcp endpoint"):
        required_environment({MCP_URL_ENV: "https://agents.example.com", TOKEN_ENV: token})
    with pytest.raises(ValueError, match="must not contain credentials"):
        required_environment(
            {MCP_URL_ENV: "https://user:pass@agents.example.com/mcp", TOKEN_ENV: token}
        )

    configured = required_environment(
        {MCP_URL_ENV: "https://agents.example.com/mcp", TOKEN_ENV: token}
    )

    assert configured.mcp_url == "https://agents.example.com/mcp"
    assert configured.token == token


def test_required_environment_requires_tls_off_loopback_and_an_agent_context_key() -> None:
    token = mint().token
    assert required_environment({MCP_URL_ENV: "http://127.0.0.1:8000/mcp", TOKEN_ENV: token})
    with pytest.raises(ValueError, match="must use HTTPS"):
        required_environment({MCP_URL_ENV: "http://agents.example.com/mcp", TOKEN_ENV: token})
    with pytest.raises(ValueError, match="dedicated Agent Context credential"):
        required_environment(
            {MCP_URL_ENV: "https://agents.example.com/mcp", TOKEN_ENV: "sk-ant-not-an-mcp-key"}
        )


def test_claude_default_scopes_exclude_high_impact_and_administrative_access() -> None:
    assert set(CLAUDE_CODE_DEFAULT_SCOPES).isdisjoint(CLAUDE_CODE_RESTRICTED_SCOPES)
    assert {"agent:manage", "research:approve", "research:promote", "content:publish"} == set(
        CLAUDE_CODE_RESTRICTED_SCOPES
    )


def test_configuration_secret_scanner_rejects_a_pasted_credential_fixture(tmp_path: Path) -> None:
    safe = project_configuration()
    leaked = {"headers": {"Authorization": f"Bearer {mint().token}"}}
    fixture = tmp_path / ".mcp.json"
    fixture.write_text(json.dumps(leaked), encoding="utf-8")

    assert contains_literal_agent_context_token(safe) is False
    candidate = json.loads(fixture.read_text(encoding="utf-8"))
    assert contains_literal_agent_context_token(candidate) is True
