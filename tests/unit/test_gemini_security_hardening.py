"""V5 Phase 7 adversarial checks for Gemini's shared MCP gateway boundary."""

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.logging import JsonFormatter
from app.integrations.gemini_cli import (
    GEMINI_CLI_DEFAULT_SCOPES,
    MCP_URL_ENV,
    TOKEN_ENV,
    contains_literal_agent_context_token,
    settings_configuration,
)
from app.models.agent import AgentClientDocument, AgentClientType
from app.models.common import utc_now
from app.security.keys import parse
from tests.security_doubles import issued_key
from tests.unit.test_claude_code_remote_http import (
    PROJECT,
    Clients,
    client,
    headers,
    initialize,
    remote_application,
    rpc,
)

GEMINI_MAIN = "gemini-cli-main"
CODEX_MAIN = "codex-main"
CLAUDE_MAIN = "claude-code-main"
PRIVATE_PROJECT = "private-project"


def _error_code(reply: dict[str, Any]) -> str:
    assert reply["result"]["isError"] is True
    return str(reply["result"]["structuredContent"]["error"]["code"])


def _context_arguments(project_id: str = PROJECT) -> dict[str, object]:
    return {
        "project_id": project_id,
        "type": "architecture_decision",
        "title": "Gemini security hardening",
        "content": "Private test content must not enter gateway activity or audit metadata.",
        "importance": 0.8,
    }


def _client(
    client_id: str,
    client_type: AgentClientType,
    scopes: tuple[str, ...],
) -> AgentClientDocument:
    return client(
        client_id=client_id,
        client_type=client_type.value,
        name=client_id,
        scopes=list(scopes),
    )


def test_gemini_settings_and_committed_fixture_scan_reject_literal_credentials(
    tmp_path: Path,
) -> None:
    """Committed examples have references only; a pasted token is detectable."""
    _, token = issued_key(scopes=("search:read",))
    committed_settings = tmp_path / ".gemini" / "settings.json"
    committed_settings.parent.mkdir()
    committed_settings.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "agent-context": {
                        "httpUrl": "${AGENT_CONTEXT_MCP_URL}",
                        "headers": {"Authorization": f"Bearer {token}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    safe_settings = settings_configuration()
    leaked_settings = json.loads(committed_settings.read_text(encoding="utf-8"))
    documentation = Path("docs/clients/gemini-cli.md").read_text(encoding="utf-8")

    assert safe_settings["mcpServers"]["agent-context"]["httpUrl"] == f"${{{MCP_URL_ENV}}}"
    assert f"Bearer ${{{TOKEN_ENV}}}" in json.dumps(safe_settings)
    assert contains_literal_agent_context_token(safe_settings) is False
    assert contains_literal_agent_context_token(leaked_settings) is True
    assert contains_literal_agent_context_token(documentation) is False


def test_gemini_credential_is_hashed_and_bare_tokens_cannot_reach_logs() -> None:
    """Credential storage and formatter redaction both reject token exposure."""
    record, token = issued_key(scopes=GEMINI_CLI_DEFAULT_SCOPES)
    secret = parse(token).secret
    log_record = logging.LogRecord(
        name="agent_context.gemini_security",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=f"Gemini transport failure carrying {token}",
        args=(),
        exc_info=None,
    )
    log_record.retrieval_error = f"gateway diagnostic {token}"

    serialized_record = record.model_dump_json()
    rendered = JsonFormatter().format(log_record)

    assert record.key_hash != token
    assert token not in serialized_record
    assert secret not in serialized_record
    assert token not in rendered
    assert secret not in rendered
    assert "[REDACTED AGENT CONTEXT TOKEN]" in rendered


@pytest.mark.asyncio
async def test_gemini_cannot_spoof_identity_scopes_actor_trust_or_client_filters() -> None:
    """Gateway authorization is derived solely from the bearer credential."""
    read_only_scopes = ("search:read", "projects:read", "agent:self")
    async with remote_application(
        scopes=read_only_scopes,
        client_id=GEMINI_MAIN,
        client_type=AgentClientType.GEMINI_CLI.value,
        client_name="Gemini CLI",
    ) as (application, token, activities, writes):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token, client_name=CODEX_MAIN)
            spoofed = {
                "X-Agent-Context-Client-ID": CODEX_MAIN,
                "X-Agent-Context-Client-Type": AgentClientType.CODEX.value,
                "X-Agent-Context-Scopes": "context:write,research:approve,content:publish",
                "X-Agent-Context-Actor": "operator",
                "X-Gemini-Trust": "true",
            }
            capabilities = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "get_platform_capabilities", "arguments": {}},
                2,
                extra_headers=spoofed,
            )
            # This direct call represents a user removing Gemini's local
            # includeTools/excludeTools restrictions and setting trust=true.
            write = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "save_context", "arguments": _context_arguments()},
                3,
                extra_headers=spoofed,
            )

    caller = capabilities["result"]["structuredContent"]["data"]["caller"]
    assert capabilities["result"]["structuredContent"]["meta"]["client_id"] == GEMINI_MAIN
    assert set(caller["scopes"]) == set(read_only_scopes)
    assert _error_code(write) == "FORBIDDEN"
    assert writes == []
    assert activities.items
    assert {(event.client_id, event.client_type) for event in activities.items} == {
        (GEMINI_MAIN, AgentClientType.GEMINI_CLI.value)
    }
    assert "Private test content" not in activities.items[-1].model_dump_json()


@pytest.mark.asyncio
async def test_gemini_cannot_bypass_project_or_high_risk_v2_v3_permissions() -> None:
    """Trusted/filter-free client configuration cannot create elevated authority."""
    scopes = (
        "projects:read",
        "context:read",
        "context:write",
        "research:read",
        "research:write",
        "agent:self",
    )
    async with remote_application(
        scopes=scopes,
        client_id=GEMINI_MAIN,
        client_type=AgentClientType.GEMINI_CLI.value,
        client_name="Gemini CLI",
        project_ids=(PROJECT,),
    ) as (application, token, _, writes):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token)
            headers_with_trust = {
                "X-Gemini-Trust": "true",
                "X-Agent-Context-Scopes": "content:publish,research:approve,research:promote",
            }
            project_bypass = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "save_context", "arguments": _context_arguments(PRIVATE_PROJECT)},
                2,
                extra_headers=headers_with_trust,
            )
            published = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {
                    "name": "mark_content_published",
                    "arguments": {
                        "profile_id": "creator-profile",
                        "content_id": "security-content",
                        "expected_revision": 1,
                    },
                },
                3,
                extra_headers=headers_with_trust,
            )
            approved = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {
                    "name": "approve_knowledge_candidate",
                    "arguments": {
                        "research_project_id": "gemini-research",
                        "candidate_id": "security-candidate",
                        "expected_revision": 1,
                        "reason": "spoofed approval",
                    },
                },
                4,
                extra_headers=headers_with_trust,
            )
            promoted = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {
                    "name": "promote_knowledge_candidate",
                    "arguments": {
                        "research_project_id": "gemini-research",
                        "candidate_id": "security-candidate",
                        "expected_revision": 1,
                    },
                },
                5,
                extra_headers=headers_with_trust,
            )

    assert _error_code(project_bypass) == "PROJECT_FORBIDDEN"
    assert _error_code(published) == "FORBIDDEN"
    assert _error_code(approved) == "FORBIDDEN"
    assert _error_code(promoted) == "FORBIDDEN"
    assert writes == []


@pytest.mark.asyncio
async def test_gemini_rejects_other_client_sessions_and_credential_lifecycle_is_independent() -> (
    None
):
    """Session ownership, revocation, expiry, and client isolation are immediate."""
    scopes = ("search:read", "projects:read", "agent:self")
    async with remote_application(
        scopes=scopes,
        client_id=GEMINI_MAIN,
        client_type=AgentClientType.GEMINI_CLI.value,
        client_name="Gemini CLI",
    ) as (application, gemini_token, _, _):
        keys = application.state.test_api_keys
        clients: Clients = application.state.test_agent_clients
        codex_record, codex_token = issued_key(scopes=scopes, projects=(PROJECT,))
        claude_record, claude_token = issued_key(scopes=scopes, projects=(PROJECT,))
        codex_record = codex_record.model_copy(update={"client_id": CODEX_MAIN})
        claude_record = claude_record.model_copy(update={"client_id": CLAUDE_MAIN})
        keys.records[codex_record.key_id] = codex_record
        keys.records[claude_record.key_id] = claude_record
        clients.items[CODEX_MAIN] = _client(CODEX_MAIN, AgentClientType.CODEX, scopes)
        clients.items[CLAUDE_MAIN] = _client(CLAUDE_MAIN, AgentClientType.CLAUDE_CODE, scopes)

        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            gemini_session = await initialize(http, gemini_token)
            cross_session = await http.post(
                "/mcp",
                headers=headers(codex_token, gemini_session),
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )

            gemini_key = parse(gemini_token).key_id
            old = keys.records[gemini_key]
            keys.records[gemini_key] = old.model_copy(
                update={"status": "revoked", "revoked_at": utc_now()}
            )
            revoked = await http.post(
                "/mcp",
                headers=headers(gemini_token, gemini_session),
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            )
            codex_session = await initialize(http, codex_token, client_name=CODEX_MAIN)
            claude_session = await initialize(http, claude_token, client_name=CLAUDE_MAIN)
            codex_ok = await rpc(http, codex_token, codex_session, "tools/list", {}, 4)
            claude_ok = await rpc(http, claude_token, claude_session, "tools/list", {}, 5)

    assert cross_session.status_code == 401
    assert cross_session.json()["error"]["code"] == "UNAUTHENTICATED"
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "UNAUTHENTICATED"
    assert codex_ok["result"]["tools"]
    assert claude_ok["result"]["tools"]


@pytest.mark.asyncio
async def test_expired_gemini_credential_and_oversized_remote_request_are_refused() -> None:
    """Expiry and request bounds are enforced before a tool can run."""
    scopes = ("search:read", "projects:read", "agent:self")
    async with remote_application(
        scopes=scopes,
        client_id=GEMINI_MAIN,
        client_type=AgentClientType.GEMINI_CLI.value,
        client_name="Gemini CLI",
        max_request_bytes=64,
    ) as (application, token, _, _):
        keys = application.state.test_api_keys
        key_id = parse(token).key_id
        stored = keys.records[key_id]
        keys.records[key_id] = stored.model_copy(
            update={"expires_at": utc_now() - timedelta(seconds=1)}
        )
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            expired = await http.post("/mcp", headers=headers(token), json={})
            oversized = await http.post(
                "/mcp",
                headers={**headers(token), "Content-Length": "65"},
                content=b"x" * 65,
            )

    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "CREDENTIAL_EXPIRED"
    assert oversized.status_code == 413
    assert oversized.json() == {"detail": "a request body may be at most 64 bytes"}
