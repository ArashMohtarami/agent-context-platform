"""Retrieval provenance accepts only complete safe MCP gateway identifiers."""

from fastapi import FastAPI
from starlette.requests import Request

from app.api.gateway_context import retrieval_metadata
from app.models.agent import AgentClientType
from app.security.principal import Principal


def principal() -> Principal:
    return Principal(
        key_id="0123456789abcdef",
        name="codex",
        scopes=("search:read",),
        client_id="codex-main",
        client_type="codex",
    )


def request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/search",
            "headers": headers,
            "app": FastAPI(),
        }
    )


def test_retrieval_metadata_records_a_complete_gateway_identity() -> None:
    metadata = retrieval_metadata(
        request(
            [
                (b"x-agent-context-client-id", b"codex-main"),
                (b"x-agent-context-client-type", b"codex"),
                (b"x-agent-context-session-id", b"session-1"),
            ]
        ),
        principal(),
    )

    assert metadata.client_id == "codex-main"
    assert metadata.client_type == AgentClientType.CODEX
    assert metadata.session_id == "session-1"


def test_spoofed_or_invalid_headers_cannot_replace_credential_identity() -> None:
    partial = retrieval_metadata(
        request([(b"x-agent-context-client-id", b"codex-main")]), principal()
    )
    spoofed = retrieval_metadata(
        request(
            [
                (b"x-agent-context-client-id", b"claude-main"),
                (b"x-agent-context-client-type", b"claude_code"),
                (b"x-agent-context-session-id", b"session-1"),
            ]
        ),
        principal(),
    )

    assert partial.client_id == "codex-main"
    assert partial.client_type == AgentClientType.CODEX
    assert spoofed.client_id == "codex-main"
    assert spoofed.client_type == AgentClientType.CODEX
    assert spoofed.session_id is None
