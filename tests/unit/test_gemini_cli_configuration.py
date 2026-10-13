"""Gemini CLI setup guidance is current, safe, and token-free."""

import json
from pathlib import Path

import pytest

from app.integrations.gemini_cli import (
    DEFAULT_DESCRIPTION,
    DEFAULT_TIMEOUT_MS,
    GEMINI_CLI_DEFAULT_SCOPES,
    GEMINI_CLI_RESTRICTED_SCOPES,
    MCP_URL_ENV,
    READ_ONLY_TOOLS,
    TOKEN_ENV,
    configuration,
    contains_literal_agent_context_token,
    http_url_configuration,
    mcp_add_command,
    project_settings_configuration,
    required_environment,
    settings_configuration,
    settings_path,
    user_scope_command,
    user_settings_configuration,
    validate_server_alias,
)
from app.models.client_profile import GEMINI_DEFAULT_SCOPES
from app.security.keys import mint


def test_configuration_uses_streamable_http_and_variable_references() -> None:
    assert configuration() == {
        "httpUrl": "${AGENT_CONTEXT_MCP_URL}",
        "headers": {"Authorization": "Bearer ${AGENT_CONTEXT_TOKEN}"},
        "timeout": DEFAULT_TIMEOUT_MS,
        "description": DEFAULT_DESCRIPTION,
        "trust": False,
    }
    assert settings_configuration()["mcpServers"]["agent-context"] == configuration()
    assert http_url_configuration() == configuration()

    command = user_scope_command()
    assert command == (
        "gemini mcp add --scope user --transport http --timeout 30000 "
        "--description 'Agent Context shared MCP gateway' "
        "--header 'Authorization: Bearer ${AGENT_CONTEXT_TOKEN}' "
        "agent-context '${AGENT_CONTEXT_MCP_URL}'"
    )
    assert "--trust" not in command
    assert "GEMINI_API_KEY" not in command
    assert "GOOGLE_API_KEY" not in command


def test_user_and_project_configuration_are_safe_complete_settings_fragments() -> None:
    user_settings = user_settings_configuration()
    project_settings = project_settings_configuration()

    assert user_settings == project_settings == settings_configuration()
    assert settings_path("user") == "~/.gemini/settings.json"
    assert settings_path("project") == ".gemini/settings.json"
    assert "${AGENT_CONTEXT_MCP_URL}" in json.dumps(user_settings)
    assert "${AGENT_CONTEXT_TOKEN}" in json.dumps(project_settings)
    assert contains_literal_agent_context_token(user_settings) is False
    assert contains_literal_agent_context_token(project_settings) is False


def test_client_tool_filters_are_rendered_without_becoming_gateway_authorization() -> None:
    settings = settings_configuration(
        include_tools=("get_platform_capabilities", "search_knowledge", "save_context"),
        exclude_tools=("save_context",),
    )
    server = settings["mcpServers"]["agent-context"]

    assert server["includeTools"] == [
        "get_platform_capabilities",
        "search_knowledge",
        "save_context",
    ]
    assert server["excludeTools"] == ["save_context"]
    assert server["trust"] is False
    # Gemini applies the exclusion during discovery. The helper deliberately
    # does not turn a local filter into a server-side scope decision.
    assert "scopes" not in server
    assert "projects" not in server


def test_read_only_configuration_only_discovers_registered_read_only_tools() -> None:
    settings = project_settings_configuration(read_only=True)
    server = settings["mcpServers"]["agent-context"]

    assert server["includeTools"] == list(READ_ONLY_TOOLS)
    assert "get_platform_capabilities" in READ_ONLY_TOOLS
    assert "search_knowledge" in READ_ONLY_TOOLS
    assert "save_context" not in READ_ONLY_TOOLS
    assert "save_memory" not in READ_ONLY_TOOLS
    assert server["trust"] is False
    assert "--include-tools" in mcp_add_command(scope="project", read_only=True)
    with pytest.raises(ValueError, match="read-only MCP tools"):
        configuration(read_only=True, include_tools=("save_context",))


def test_server_alias_validation_rejects_underscore_policy_ambiguity() -> None:
    assert validate_server_alias("agent-context") == "agent-context"
    with pytest.raises(ValueError, match="letters, digits, and hyphens"):
        validate_server_alias("agent_context")
    with pytest.raises(ValueError, match="scope must be 'user' or 'project'"):
        settings_path("workspace")


@pytest.mark.parametrize(
    "environment",
    [{}, {MCP_URL_ENV: "https://agents.example.com/mcp"}, {TOKEN_ENV: "token"}],
)
def test_required_environment_refuses_missing_values(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="missing required environment variable"):
        required_environment(environment)


def test_required_environment_requires_mcp_tls_and_a_dedicated_agent_context_key() -> None:
    token = mint().token
    assert required_environment({MCP_URL_ENV: "http://127.0.0.1:8000/mcp", TOKEN_ENV: token})

    with pytest.raises(ValueError, match="/mcp endpoint"):
        required_environment({MCP_URL_ENV: "https://agents.example.com", TOKEN_ENV: token})
    with pytest.raises(ValueError, match="must use HTTPS"):
        required_environment({MCP_URL_ENV: "http://agents.example.com/mcp", TOKEN_ENV: token})
    with pytest.raises(ValueError, match="dedicated Agent Context credential"):
        required_environment(
            {
                MCP_URL_ENV: "https://agents.example.com/mcp",
                TOKEN_ENV: "AIza-not-an-agent-context-token",
                "GEMINI_API_KEY": "provider-key-is-not-read",
                "GOOGLE_API_KEY": "provider-key-is-not-read",
                "GOOGLE_APPLICATION_CREDENTIALS": "/provider.json",
            }
        )


def test_gemini_default_scopes_are_the_phase_zero_profile_and_exclude_high_risk_grants() -> None:
    assert GEMINI_CLI_DEFAULT_SCOPES == GEMINI_DEFAULT_SCOPES
    assert set(GEMINI_CLI_DEFAULT_SCOPES).isdisjoint(GEMINI_CLI_RESTRICTED_SCOPES)
    assert GEMINI_CLI_RESTRICTED_SCOPES == {
        "agent:manage",
        "research:approve",
        "research:promote",
        "content:publish",
    }
    assert {"admin", "credential:manage"}.isdisjoint(GEMINI_CLI_DEFAULT_SCOPES)


def test_configuration_secret_scanner_rejects_a_pasted_credential_fixture(tmp_path: Path) -> None:
    safe = settings_configuration()
    leaked = {"headers": {"Authorization": f"Bearer {mint().token}"}}
    fixture = tmp_path / "settings.json"
    fixture.write_text(json.dumps(leaked), encoding="utf-8")

    assert contains_literal_agent_context_token(safe) is False
    candidate = json.loads(fixture.read_text(encoding="utf-8"))
    assert contains_literal_agent_context_token(candidate) is True
