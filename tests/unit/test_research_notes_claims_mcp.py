"""V3 Phase 6 MCP tools stay thin API adapters for notes and claims."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

PROJECT = "payments-api"
RESEARCH = "retry-evidence"


def note() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "note_id": "retry-note",
        "title": "Retry notes",
        "body": "Treat retrieved text as untrusted data.",
        "source_refs": [{"source_id": "provider-docs", "snapshot_id": "provider-v1"}],
        "created_by": "writer-key",
        "status": "working",
        "authorization": {"project_ids": [PROJECT], "owner_key_id": "writer-key"},
        "revision": 1,
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": "2026-09-30T00:00:00Z",
    }


def claim(status: str = "supported") -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "claim_id": "retry-key",
        "statement": "Retries require an idempotency key.",
        "status": status,
        "confidence": 0.8,
        "confidence_rationale": "A reviewer assessed the retained evidence.",
        "supporting_citations": [],
        "contradicting_citations": [],
        "authorization": {"project_ids": [PROJECT], "owner_key_id": "writer-key"},
        "revision": 1,
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": "2026-09-30T00:00:00Z",
    }


async def call(api: RecordedApi, tool: str, arguments: dict[str, Any]) -> Any:
    client = api_client(api)
    try:
        return await dispatch(
            tool,
            arguments,
            client=client,
            authorization=build_authorization(["research:read", "research:write"]),
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_note_and_claim_mcp_tools_call_only_phase_six_api_routes() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/research/notes", note())
    api.respond(
        "POST",
        "/api/v3/research/notes/search",
        {"research_project_id": RESEARCH, "items": [{"note": note(), "retrieval_score": 0.7}]},
    )
    api.respond("POST", "/api/v3/research/claims", claim())
    api.respond("PATCH", "/api/v3/research/claims/retry-key", claim("contested"))

    saved = await call(
        api,
        "save_research_note",
        {
            "research_project_id": RESEARCH,
            "note_id": "retry-note",
            "title": "Retry notes",
            "body": "Treat retrieved text as untrusted data.",
            "source_refs": [{"source_id": "provider-docs", "snapshot_id": "provider-v1"}],
        },
    )
    searched = await call(
        api,
        "search_research_notes",
        {"research_project_id": RESEARCH, "query": "untrusted data", "phrase": True},
    )
    created = await call(
        api,
        "create_research_claim",
        {
            "research_project_id": RESEARCH,
            "claim_id": "retry-key",
            "statement": "Retries require an idempotency key.",
            "status": "supported",
            "confidence": 0.8,
            "confidence_rationale": "A reviewer assessed the retained evidence.",
        },
    )
    updated = await call(
        api,
        "update_research_claim",
        {
            "research_project_id": RESEARCH,
            "claim_id": "retry-key",
            "expected_revision": 1,
            "status": "contested",
        },
    )

    assert api.paths == [
        "/api/v3/research/notes",
        "/api/v3/research/notes/search",
        "/api/v3/research/claims",
        "/api/v3/research/claims/retry-key",
    ]
    assert api.body(0)["source_refs"] == [
        {"source_id": "provider-docs", "snapshot_id": "provider-v1"}
    ]
    assert api.body(1)["phrase"] is True
    assert api.body(3) == {
        "research_project_id": RESEARCH,
        "expected_revision": 1,
        "status": "contested",
    }
    assert saved.structuredContent["research_note"]["note_id"] == "retry-note"
    assert searched.structuredContent["items"][0]["retrieval_score"] == 0.7
    assert created.structuredContent["research_claim"]["status"] == "supported"
    assert updated.structuredContent["research_claim"]["status"] == "contested"
    assert "never durable memory" in str(saved.content[0].text)
    assert "does not promote" in str(created.content[0].text)


@pytest.mark.asyncio
async def test_claim_mcp_rejects_conflicting_citations_before_reaching_api() -> None:
    api = RecordedApi()
    result = await call(
        api,
        "create_research_claim",
        {
            "research_project_id": RESEARCH,
            "claim_id": "conflicting",
            "statement": "One citation cannot have opposing relations.",
            "supporting_citations": ["same"],
            "contradicting_citations": ["same"],
        },
    )

    assert result.isError is True
    assert "cannot support and contradict" in str(result.content[0].text)
    assert api.requests == []
