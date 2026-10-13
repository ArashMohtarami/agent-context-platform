"""V3 Phase 8 MCP research-brief tool tests."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

PROJECT = "payments-api"
RESEARCH = "retry-evidence"


def response() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "brief_id": "retry-summary",
        "question": "What constrains safe payment retries?",
        "scope": "Provider documentation.",
        "key_findings": ["Retries use an idempotency key."],
        "claim_refs": [
            {
                "claim_id": "retry-key",
                "statement": "Retries use an idempotency key.",
                "status": "supported",
                "confidence": 0.9,
            }
        ],
        "citation_refs": [
            {
                "citation_id": "provider-support",
                "claim_id": "retry-key",
                "source_id": "provider-docs",
                "snapshot_id": "provider-v1",
                "chunk_id": "retry-chunk",
                "source_class": "official_documentation",
                "locator": {"url": "https://example.test/retries", "page": 2},
                "evidence_excerpt": "Use an idempotency key.",
                "relation": "supports",
            }
        ],
        "contradictions": [],
        "unknowns": ["This brief is structured evidence material and does not claim completeness."],
        "sources": [
            {"source_id": "provider-docs", "snapshot_id": "provider-v1", "chunk_id": "retry-chunk"}
        ],
        "next_questions": ["Which versions enforce the requirement?"],
        "status": "draft",
        "authorization": {"project_ids": [PROJECT], "owner_key_id": "writer-key"},
        "revision": 1,
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": "2026-09-30T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_build_research_brief_mcp_is_a_single_constrained_api_write() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/research/briefs", response(), status_code=201)
    client = api_client(api)
    try:
        result = await dispatch(
            "build_research_brief",
            {
                "research_project_id": RESEARCH,
                "brief_id": "retry-summary",
                "scope": "Provider documentation.",
                "additional_unknowns": ["SDK coverage is incomplete."],
                "next_questions": ["Which versions enforce the requirement?"],
            },
            client=client,
            authorization=build_authorization(["research:write"]),
        )
    finally:
        await client.close()

    assert result.isError is not True
    assert api.paths == ["/api/v3/research/briefs"]
    assert api.body() == {
        "research_project_id": RESEARCH,
        "brief_id": "retry-summary",
        "scope": "Provider documentation.",
        "additional_unknowns": ["SDK coverage is incomplete."],
        "next_questions": ["Which versions enforce the requirement?"],
        "status": "draft",
    }
    assert (
        result.structuredContent["research_brief"]["citation_refs"][0]["snapshot_id"]
        == "provider-v1"
    )
    assert "structured evidence material" in str(result.content[0].text)


@pytest.mark.asyncio
async def test_build_research_brief_mcp_rejects_unknown_or_duplicate_arguments() -> None:
    api = RecordedApi()
    client = api_client(api)
    try:
        result = await dispatch(
            "build_research_brief",
            {
                "research_project_id": RESEARCH,
                "brief_id": "retry-summary",
                "scope": "Provider documentation.",
                "next_questions": ["What next?", "What next?"],
                "command": "not allowed",
            },
            client=client,
            authorization=build_authorization(["research:write"]),
        )
    finally:
        await client.close()

    assert result.isError is True
    assert "invalid arguments" in str(result.content[0].text)
    assert api.requests == []
