"""Pure recipe/input/report contracts; actual authority and transports run separately."""

import json
import ssl
import tomllib

import pytest
from pydantic import ValidationError

from app.cli.contextctl import main
from app.core.config import McpSettings
from app.integrations.connection import (
    ConnectionSelection,
    ProbeSelection,
    preview,
    read_only_scopes,
)
from app.integrations.doctor import (
    DiagnosisError,
    credential_read_only,
    failure_code,
    tls_context,
    token_value,
)
from app.mcp.server import build_client
from app.security.keys import mint


@pytest.mark.parametrize("integration", ["codex", "claude-code", "gemini-cli", "custom"])
def test_preview_is_offline_and_uses_existing_contracts(integration, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Preview must not read this credential or any active client configuration.
    (tmp_path / ".env").write_text("CONTEXTCTL_API_BASE_URL=not-a-url\n")
    selected = ConnectionSelection(
        integration_id=integration, origin="https://127.0.0.1:9443", project_id="fictional"
    )
    data = preview(selected)
    assert data["preview_only"] and not data["configuration_applied"] and not data["policy_changed"]
    assert not data["server_read_only_verified"]
    assert "--connect" in data["doctor_command"]
    config = data["configuration"]
    parsed = (
        tomllib.loads(data["configuration_text"])
        if integration == "codex"
        else json.loads(data["configuration_text"])
    )
    assert parsed == config
    if integration == "codex":
        server = config["mcp_servers"]["agent-context"]
        assert server["env"]["MCP_API_BASE_URL"] == selected.origin
        assert server["env"]["MCP_API_CA_FILE"].endswith("reviewed-ca.pem")
    elif integration == "custom":
        assert config["url"] == selected.origin + "/mcp"
    else:
        server = config["mcpServers"]["agent-context"]
        assert (
            server["httpUrl" if integration == "gemini-cli" else "url"] == selected.origin + "/mcp"
        )
        assert server["headers"]["Authorization"] == "Bearer ${AGENT_CONTEXT_TOKEN}"
    assert not list(tmp_path.glob("**/config.toml"))


@pytest.mark.parametrize(
    "origin",
    [
        "https://private:SECRET@example.test",
        "https://example.test/?token=SECRET",
        "https://example.test/#SECRET",
        "https://example.test/mcp",
        "https://example.test:garbage",
        "http://remote.example.test",
        "https://example.test\nSECRET",
        "https://example.test\\SECRET",
        "file:///SECRET",
    ],
)
def test_invalid_inputs_are_redacted(origin, capsys):
    assert (
        main(
            [
                "--json",
                "integration",
                "doctor",
                "codex",
                "--origin",
                origin,
                "--project-id",
                "fixture",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert not output.out
    assert "CONNECTION_SELECTION_INVALID" in output.err
    assert "SECRET" not in output.err and origin not in output.err


@pytest.mark.parametrize(
    "extra",
    [
        {"write_probe": True},
        {"disposable_project_id": "fixture"},
        {"write_probe": True, "disposable_project_id": "another"},
    ],
)
def test_write_probe_requires_matching_disposable_project(extra):
    with pytest.raises(ValidationError):
        ProbeSelection(
            integration_id="codex", origin="https://example.test", project_id="fixture", **extra
        )


@pytest.mark.parametrize(
    "kind", ["absent", "directory", "symlink", "public", "oversize", "malformed"]
)
def test_private_token_file_refusals(kind, tmp_path):
    destination = tmp_path / "recipient-key"
    if kind == "directory":
        destination.mkdir()
    elif kind == "symlink":
        actual = tmp_path / "actual"
        actual.write_text(mint().token)
        actual.chmod(0o600)
        destination.symlink_to(actual)
    elif kind != "absent":
        destination.write_text(
            "x" * 513 if kind == "oversize" else "SECRET" if kind == "malformed" else mint().token
        )
        destination.chmod(0o644 if kind == "public" else 0o600)
    with pytest.raises(DiagnosisError) as failure:
        token_value(destination)
    assert str(failure.value) == "CREDENTIAL_SOURCE_INVALID"
    assert str(tmp_path) not in str(failure.value)


def test_preview_ignores_deployment_settings_and_does_not_read_token(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("CONTEXTCTL_API_BASE_URL=SECRET\n")
    monkeypatch.setenv("CONTEXTCTL_API_BASE_URL", "SECRET")
    monkeypatch.setenv("MCP_API_TOKEN_FILE", "/nonexistent/SECRET")
    assert (
        main(
            [
                "--json",
                "integration",
                "doctor",
                "codex",
                "--origin",
                "https://example.test",
                "--project-id",
                "fixture",
                "--token-file",
                "/nonexistent/SECRET",
            ]
        )
        == 0
    )
    data = capsys.readouterr().out
    assert "SECRET" not in data
    assert json.loads(data)["preview_only"]


@pytest.mark.parametrize("optional", [False, True])
def test_failed_preflight_has_nonzero_exit_and_no_raw_error(capsys, optional):
    assert (
        main(
            [
                "--json",
                "integration",
                "doctor",
                "codex",
                "--origin",
                "https://example.test",
                "--project-id",
                "fixture",
                "--connect",
                *(
                    ["--require-read-only", "--write-probe", "--disposable-project-id", "fixture"]
                    if optional
                    else []
                ),
            ]
        )
        == 1
    )
    data = json.loads(capsys.readouterr().out)
    assert not data["ready"] and not data["network_checked"]
    assert data["checks"][0]["code"] == "CREDENTIAL_SOURCE_REQUIRED"
    assert all(c["status"] != "passed" for c in data["checks"])

    statuses = {c["stage"]: c["status"] for c in data["checks"]}
    assert statuses["policy"] == ("not_run" if optional else "not_requested")
    assert statuses["write"] == ("not_run" if optional else "not_requested")


def test_local_tool_filter_is_not_server_read_only_policy():
    assert read_only_scopes(["context:read", "agent:self"])
    for scopes in (
        [],
        ["context:read", "context:write"],
        ["research:approve"],
        ["session:write"],
        ["unknown:read"],
    ):
        assert not read_only_scopes(scopes)


@pytest.mark.asyncio
async def test_stdio_custom_ca_retains_verified_tls(tmp_path):
    # The provided context remains verified, and omitted trust uses httpx's normal CA bundle.
    default = build_client(McpSettings(_env_file=None))
    assert default._client._transport._pool._ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert default._client._transport._pool._ssl_context.check_hostname
    await default.close()
    # An invalid explicit CA must fail rather than fall back to ignoring verification.
    bad = tmp_path / "bad-ca"
    bad.write_text("not a certificate")
    with pytest.raises(ssl.SSLError):
        build_client(McpSettings(_env_file=None, mcp_api_ca_file=bad))


def test_evaluation_discovery_intent_stays_in_instance_org():
    from scripts.evaluation_lifecycle import root_grants

    grants = root_grants("a" * 32)
    discovery = [g for g in grants if g["action"] == "agent:self"]
    assert discovery == [
        {
            "action": "agent:self",
            "resource_kind": "organization_knowledge",
            "selector": "include",
            "ids": ["eval-" + "a" * 32],
            "conditions": {},
        }
    ]
    assert not any(
        g["action"] in {"agent:manage", "session:write", "research:approve"} for g in grants
    )


def test_pasted_credential_is_not_a_project_recipe(capsys):
    token = mint().token
    assert (
        main(
            [
                "--json",
                "integration",
                "doctor",
                "codex",
                "--origin",
                "https://example.test",
                "--project-id",
                token,
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--token", "SECRET"],
        ["--header", "Authorization: Bearer SECRET"],
        ["--token-file", "/private/SECRET", "--unknown"],
        ["--timeout", "SECRET"],
    ],
)
def test_doctor_argument_errors_never_echo_private_input(arguments, capsys):
    assert (
        main(
            [
                "--json",
                "integration",
                "doctor",
                "codex",
                "--origin",
                "https://example.test",
                "--project-id",
                "fixture",
                *arguments,
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "CONNECTION_ARGUMENTS_INVALID" in captured.err
    assert "SECRET" not in captured.out + captured.err


@pytest.mark.parametrize("kind", ["symlink", "directory", "oversize", "malformed", "fifo"])
def test_ca_input_is_bounded_before_network(kind, tmp_path):
    target = tmp_path / "ca"
    if kind == "symlink":
        target.symlink_to("/dev/zero")
    elif kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        import os

        os.mkfifo(target)
    else:
        target.write_text("SECRET" if kind == "malformed" else "x" * (1024 * 1024 + 1))
    with pytest.raises(DiagnosisError) as failure:
        tls_context(target)
    assert str(failure.value) == "CA_TRUST_INVALID"


def test_read_only_scope_projection_cannot_hide_mutating_tuple():
    from app.schemas.gateway import GatewayIdentityResponse
    from app.security.policy.models import Grant

    identity = GatewayIdentityResponse(
        org_id="fixture",
        key_id="fictional",
        client_id="worker",
        client_type="codex",
        scopes=("context:read",),
        allowed_project_ids=("fixture",),
        allowed_profile_ids=(),
        action_resources=(
            Grant(
                action="context:write", resource_kind="project", selector="include", ids=["fixture"]
            ),
        ),
    )
    assert not credential_read_only(identity)
    assert credential_read_only(identity.model_copy(update={"action_resources": None}))


def test_sdk_wrapped_errors_use_fixed_codes_without_echoing_messages():
    error = ExceptionGroup(
        "SECRET /private/path",
        [ValueError("Authorization: SECRET"), ExceptionGroup("raw", [DiagnosisError("FORBIDDEN")])],
    )
    assert failure_code(error) == "FORBIDDEN"
    assert failure_code(ExceptionGroup("SECRET", [TimeoutError("SECRET")])) == "DEADLINE_EXCEEDED"
    assert failure_code(ValueError("SECRET")) == "CHECK_FAILED"


def test_uppercase_https_is_canonical_for_transport_reporting():
    selection = ConnectionSelection(
        integration_id="codex", origin="HTTPS://example.test/", project_id="fixture"
    )
    assert selection.origin == "https://example.test"
