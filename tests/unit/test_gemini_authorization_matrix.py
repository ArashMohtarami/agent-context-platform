"""V5 Phase 3: Gemini uses the existing V4 gateway authorization matrix."""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.integrations.gemini_cli import configuration
from app.models.client_profile import GEMINI_DEFAULT_SCOPES
from tests.unit.test_claude_code_remote_http import PROJECT, initialize, remote_application, rpc

OTHER_PROJECT = "sampleco-wallet"
GEMINI_MAIN = "gemini-cli-main"
GEMINI_READONLY = "gemini-cli-readonly"
GEMINI_RESEARCH = "gemini-cli-research"
READ_ONLY_SCOPES = (
    "skills:read",
    "projects:read",
    "context:read",
    "memory:read",
    "search:read",
    "research:read",
    "agent:self",
    "audit:self:read",
)
RESEARCH_SCOPES = (
    "skills:read",
    "projects:read",
    "context:read",
    "memory:read",
    "search:read",
    "research:read",
    "research:write",
    "agent:self",
    "session:write",
    "audit:self:read",
)


def _error_code(reply: dict[str, Any]) -> str:
    assert reply["result"]["isError"] is True
    return str(reply["result"]["structuredContent"]["error"]["code"])


def _context_arguments(project_id: str = PROJECT) -> dict[str, object]:
    return {
        "project_id": project_id,
        "type": "architecture_decision",
        "title": "Gemini authorization matrix",
        "content": "The gateway authorization is derived from the dedicated credential.",
        "importance": 0.8,
    }


def _memory_arguments() -> dict[str, object]:
    return {
        "type": "convention",
        "content": "Use the gateway authorization matrix for every MCP client.",
        "importance": 0.8,
        "confidence": 0.9,
        "project_id": PROJECT,
    }


def _candidate_arguments(project_id: str = PROJECT) -> dict[str, object]:
    return {
        "research_project_id": "gemini-research",
        "candidate_id": "gemini-gateway-boundary",
        "candidate_type": "project_context",
        "target_project_id": project_id,
        "title": "Gateway authorization boundary",
        "content": "Gemini configuration cannot grant a server-side capability.",
        "source_claim_ids": ["gateway-authorization"],
        "source_citation_ids": ["v4-gateway"],
        "confidence": 0.9,
        "confidence_rationale": "The live gateway derives authorization from the credential.",
    }


def _promotion_arguments() -> dict[str, object]:
    return {
        "research_project_id": "gemini-research",
        "candidate_id": "gemini-gateway-boundary",
        "expected_revision": 1,
    }


def _publication_arguments() -> dict[str, object]:
    return {
        "profile_id": "creator-profile",
        "content_id": "gateway-post",
        "expected_revision": 1,
    }


def test_gemini_default_scopes_are_least_privilege_and_have_no_high_risk_grants() -> None:
    allowed = {
        "skills:read",
        "projects:read",
        "context:read",
        "context:write",
        "memory:read",
        "memory:write",
        "search:read",
        "research:read",
        "research:write",
    }
    restricted = {
        "admin",
        "agent:manage",
        "research:approve",
        "research:promote",
        "content:publish",
        "credential:manage",
    }

    assert allowed <= set(GEMINI_DEFAULT_SCOPES)
    assert set(GEMINI_DEFAULT_SCOPES).isdisjoint(restricted)


@pytest.mark.asyncio
async def test_readonly_gemini_can_search_but_cannot_write_v1_records() -> None:
    async with remote_application(
        scopes=READ_ONLY_SCOPES,
        client_id=GEMINI_READONLY,
        client_type="gemini_cli",
        client_name="Gemini CLI read-only",
    ) as (application, token, _, writes):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token)
            searched = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "search_knowledge", "arguments": {"query": "gateway"}},
                2,
            )
            context_write = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "save_context", "arguments": _context_arguments()},
                3,
            )
            memory_write = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "save_memory", "arguments": _memory_arguments()},
                4,
            )

    assert searched["result"]["isError"] is False
    assert _error_code(context_write) == "FORBIDDEN"
    assert _error_code(memory_write) == "FORBIDDEN"
    assert writes == []


@pytest.mark.asyncio
async def test_normal_gemini_uses_v1_v3_and_v4_tools_but_cannot_promote_or_publish() -> None:
    async with remote_application(
        scopes=GEMINI_DEFAULT_SCOPES,
        client_id=GEMINI_MAIN,
        client_type="gemini_cli",
        client_name="Gemini CLI",
    ) as (application, token, _, writes):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token)
            # V4 capability discovery returns the effective gateway grant.
            capabilities = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "get_platform_capabilities", "arguments": {}},
                2,
            )
            # V1 durable context and memory writes use the shared gateway.
            context_write = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "save_context", "arguments": _context_arguments()},
                3,
            )
            memory_write = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "save_memory", "arguments": _memory_arguments()},
                4,
            )
            # V3 may propose a candidate, but promotion remains review-gated.
            proposed = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "propose_knowledge_candidate", "arguments": _candidate_arguments()},
                5,
            )
            promoted = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "promote_knowledge_candidate", "arguments": _promotion_arguments()},
                6,
            )
            # V2 publication is a separate elevated authority.
            published = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "mark_content_published", "arguments": _publication_arguments()},
                7,
            )

    assert capabilities["result"]["isError"] is False
    caller = capabilities["result"]["structuredContent"]["data"]["caller"]
    assert set(caller["scopes"]) == set(GEMINI_DEFAULT_SCOPES)
    assert capabilities["result"]["structuredContent"]["meta"]["client_id"] == GEMINI_MAIN
    assert context_write["result"]["isError"] is False
    assert memory_write["result"]["isError"] is False
    assert proposed["result"]["isError"] is False
    assert _error_code(promoted) == "FORBIDDEN"
    assert _error_code(published) == "FORBIDDEN"
    assert writes[0]["title"] == "Gemini authorization matrix"


@pytest.mark.asyncio
async def test_research_gemini_can_propose_but_cannot_approve_or_promote() -> None:
    async with remote_application(
        scopes=RESEARCH_SCOPES,
        client_id=GEMINI_RESEARCH,
        client_type="gemini_cli",
        client_name="Gemini CLI research",
    ) as (application, token, _, _):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token)
            proposed = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "propose_knowledge_candidate", "arguments": _candidate_arguments()},
                2,
            )
            approved = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {
                    "name": "approve_knowledge_candidate",
                    "arguments": {**_promotion_arguments(), "reason": "reviewed"},
                },
                3,
            )
            promoted = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "promote_knowledge_candidate", "arguments": _promotion_arguments()},
                4,
            )

    assert proposed["result"]["isError"] is False
    assert _error_code(approved) == "FORBIDDEN"
    assert _error_code(promoted) == "FORBIDDEN"


@pytest.mark.asyncio
async def test_gemini_project_grants_protect_v1_and_v3_target_projects() -> None:
    async with remote_application(
        scopes=GEMINI_DEFAULT_SCOPES,
        client_id=GEMINI_MAIN,
        client_type="gemini_cli",
        client_name="Gemini CLI",
        project_ids=(PROJECT,),
    ) as (application, token, _, writes):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token)
            context_write = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "save_context", "arguments": _context_arguments(OTHER_PROJECT)},
                2,
            )
            candidate = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {
                    "name": "propose_knowledge_candidate",
                    "arguments": _candidate_arguments(OTHER_PROJECT),
                },
                3,
            )

    assert _error_code(context_write) == "PROJECT_FORBIDDEN"
    assert _error_code(candidate) == "PROJECT_FORBIDDEN"
    assert writes == []


@pytest.mark.asyncio
async def test_gemini_filter_trust_client_and_identity_spoofing_cannot_elevate() -> None:
    """Only the bearer-derived V4 identity reaches the authorization decision."""
    filtered = configuration(
        include_tools=("search_knowledge",),
        exclude_tools=("save_context",),
    )
    trusted_unfiltered = dict(filtered)
    trusted_unfiltered.pop("includeTools")
    trusted_unfiltered.pop("excludeTools")
    trusted_unfiltered["trust"] = True
    assert trusted_unfiltered["trust"] is True
    assert "includeTools" not in trusted_unfiltered
    assert "excludeTools" not in trusted_unfiltered

    # These are local Gemini controls. The MCP protocol transmits no client
    # configuration as authorization input, so a direct protocol caller can
    # still use a tool Gemini chose to hide when its bearer permits it.
    async with remote_application(
        scopes=GEMINI_DEFAULT_SCOPES,
        client_id=GEMINI_MAIN,
        client_type="gemini_cli",
        client_name="Gemini CLI",
    ) as (application, token, _, _):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token)
            filtered_write = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "save_context", "arguments": _context_arguments()},
                2,
            )

    assert filtered_write["result"]["isError"] is False

    # Conversely, removing filters and trusting the server cannot expand a
    # read-only credential's gateway grant.
    spoofed_headers = {
        "X-Agent-Context-Client-ID": GEMINI_MAIN,
        "X-Agent-Context-Scopes": "research:approve,research:promote,content:publish",
    }
    async with remote_application(
        scopes=READ_ONLY_SCOPES,
        client_id=GEMINI_READONLY,
        client_type="gemini_cli",
        client_name="Gemini CLI read-only",
    ) as (application, token, _, _):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        ) as http:
            session_id = await initialize(http, token, client_name=GEMINI_MAIN)
            capabilities = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "get_platform_capabilities", "arguments": {}},
                2,
                extra_headers=spoofed_headers,
            )
            promotion = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "promote_knowledge_candidate", "arguments": _promotion_arguments()},
                3,
                extra_headers=spoofed_headers,
            )
            context_write = await rpc(
                http,
                token,
                session_id,
                "tools/call",
                {"name": "save_context", "arguments": _context_arguments()},
                4,
                extra_headers=spoofed_headers,
            )

    assert capabilities["result"]["isError"] is False
    assert capabilities["result"]["structuredContent"]["meta"]["client_id"] == GEMINI_READONLY
    caller_scopes = set(capabilities["result"]["structuredContent"]["data"]["caller"]["scopes"])
    assert caller_scopes == set(READ_ONLY_SCOPES)
    assert {"research:approve", "research:promote", "content:publish"}.isdisjoint(caller_scopes)
    assert _error_code(promotion) == "FORBIDDEN"
    assert _error_code(context_write) == "FORBIDDEN"
