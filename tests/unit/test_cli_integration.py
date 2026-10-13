"""Integration CLI helpers print safe, token-free configuration guidance."""

import json

from app.cli.contextctl import main


def test_claude_code_setup_prints_references_without_modifying_configuration(
    capsys: object,
) -> None:
    assert main(["--json", "integration", "claude-code", "setup"]) == 0

    captured = capsys.readouterr()  # type: ignore[attr-defined]

    assert "AGENT_CONTEXT_MCP_URL" in captured.out
    assert "AGENT_CONTEXT_TOKEN" in captured.out
    assert "${AGENT_CONTEXT_TOKEN}" in captured.out
    assert "ANTHROPIC_AUTH_TOKEN" not in captured.out


def test_gemini_cli_setup_prints_current_safe_http_guidance_without_modifying_configuration(
    capsys: object,
) -> None:
    assert (
        main(
            [
                "--json",
                "integration",
                "gemini-cli",
                "setup",
                "--scope",
                "project",
                "--read-only",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    server = payload["settings"]["mcpServers"]["agent-context"]

    assert "AGENT_CONTEXT_MCP_URL" in captured.out
    assert "AGENT_CONTEXT_TOKEN" in captured.out
    assert "gemini mcp add --scope project --transport http" in captured.out
    assert "${AGENT_CONTEXT_TOKEN}" in captured.out
    assert payload["scope"] == "project"
    assert payload["settings_path"] == ".gemini/settings.json"
    assert payload["read_only"] is True
    assert server["httpUrl"] == "${AGENT_CONTEXT_MCP_URL}"
    assert "includeTools" in server
    assert '"trust": false' in captured.out
    assert "acp_" not in captured.out
    assert "GEMINI_API_KEY" not in captured.out
    assert "GOOGLE_API_KEY" not in captured.out


def test_gemini_cli_show_json_emits_only_a_safe_user_settings_fragment(capsys: object) -> None:
    assert main(["integration", "gemini-cli", "setup", "--scope", "user", "--show-json"]) == 0

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    settings = json.loads(captured.out)

    assert settings["mcpServers"]["agent-context"]["httpUrl"] == "${AGENT_CONTEXT_MCP_URL}"
    assert settings["mcpServers"]["agent-context"]["trust"] is False
    assert "AGENT_CONTEXT_TOKEN" in captured.out
    assert "gemini mcp add" not in captured.out


def test_generic_integration_list_and_show_are_token_free(capsys: object) -> None:
    assert main(["--json", "integration", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)

    assert {item["integration_id"] for item in listed["integrations"]} == {
        "codex",
        "claude-code",
        "gemini-cli",
        "custom",
    }

    for integration_id, client_type in (
        ("codex", "codex"),
        ("claude-code", "claude_code"),
        ("gemini-cli", "gemini_cli"),
    ):
        assert main(["--json", "integration", "show", integration_id]) == 0
        shown = capsys.readouterr().out
        payload = json.loads(shown)

        assert payload["integration"]["client_type"] == client_type
        assert "acp_" not in shown
        if integration_id != "codex":
            assert "AGENT_CONTEXT_TOKEN" in shown

    assert payload["configuration"]["mcpServers"]["agent-context"]["trust"] is False


def test_generic_integration_validate_is_a_local_secret_redacting_preflight(
    capsys: object, monkeypatch: object
) -> None:
    monkeypatch.delenv("AGENT_CONTEXT_MCP_URL", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("AGENT_CONTEXT_TOKEN", raising=False)  # type: ignore[attr-defined]

    assert main(["--json", "integration", "validate", "gemini-cli"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ready"] is False
    assert payload["network_checked"] is False
    assert {check["status"] for check in payload["checks"]} == {"missing"}
    assert "acp_" not in json.dumps(payload)
