"""V3 Phase 7 MCP evidence tools remain thin authorized REST adapters."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

PROJECT = "payments-api"
RESEARCH = "retry-evidence"


def citation_item(relation: str = "supports") -> dict[str, Any]:
    return {
        "citation": {
            "research_project_id": RESEARCH,
            "citation_id": f"{relation}-guide",
            "claim_id": "retry-key",
            "source_id": "provider-docs",
            "snapshot_id": "provider-v1",
            "chunk_id": "retry-chunk",
            "source_class": "official_documentation",
            "locator": {"url": "https://example.test/retries", "page": 2, "line_start": 10},
            "evidence_excerpt": "Use an idempotency key for retryable requests.",
            "relation": relation,
            "status": "active",
            "authorization": {"project_ids": [PROJECT], "owner_key_id": "writer-key"},
            "revision": 1,
            "created_at": "2026-09-30T00:00:00Z",
            "updated_at": "2026-09-30T00:00:00Z",
        },
        "claim_id": "retry-key",
        "claim_statement": "Retries require idempotency keys.",
        "claim_status": "contested",
        "source": {
            "source_id": "provider-docs",
            "snapshot_id": "provider-v1",
            "uri": "https://example.test/retries",
            "canonical_uri": "https://example.test/retries",
            "title": "Provider retry guide",
            "publisher": "Example provider",
            "author": None,
            "published_at": "2026-09-29T00:00:00Z",
            "retrieved_at": "2026-09-30T00:00:00Z",
            "source_type": "url",
            "source_class": "official_documentation",
        },
    }


def packet() -> dict[str, Any]:
    item = citation_item()
    contradicting = citation_item("contradicts")
    return {
        "question": "What constrains retries?",
        "research_project_id": RESEARCH,
        "relevant_context": [],
        "supporting_evidence": [item],
        "contradicting_evidence": [contradicting],
        "qualifying_evidence": [],
        "background_evidence": [],
        "source_metadata": [item["source"]],
        "citation_locators": [item["citation"]["locator"], contradicting["citation"]["locator"]],
        "conflicts": [
            {
                "claim_id": "retry-key",
                "statement": "Retries require idempotency keys.",
                "supporting_evidence": [item],
                "contradicting_evidence": [contradicting],
            }
        ],
        "uncertainties": [
            "Persisted evidence conflicts for claim retry-key; no side was selected."
        ],
        "token_budget": 8000,
        "tokens_used": 42,
    }


async def call(api: RecordedApi, tool: str, arguments: dict[str, Any]) -> Any:
    client = api_client(api)
    try:
        return await dispatch(
            tool,
            arguments,
            client=client,
            authorization=build_authorization(["research:read"]),
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_evidence_mcp_tools_use_only_constrained_rest_routes() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/research/evidence", packet())
    api.respond(
        "GET",
        "/api/v3/research/claims/retry-key/evidence",
        {
            "research_project_id": RESEARCH,
            "claim_id": "retry-key",
            "supporting_evidence": [citation_item()],
            "contradicting_evidence": [citation_item("contradicts")],
            "qualifying_evidence": [],
            "background_evidence": [],
        },
    )

    built = await call(
        api,
        "build_evidence_packet",
        {
            "query": "retry idempotency",
            "research_project_id": RESEARCH,
            "project_id": PROJECT,
            "token_budget": 500,
        },
    )
    shown = await call(
        api,
        "get_claim_evidence",
        {"research_project_id": RESEARCH, "claim_id": "retry-key"},
    )

    assert api.paths == [
        "/api/v3/research/evidence",
        "/api/v3/research/claims/retry-key/evidence",
    ]
    assert api.body(0) == {
        "query": "retry idempotency",
        "research_project_id": RESEARCH,
        "project_id": PROJECT,
        "token_budget": 500,
    }
    assert api.query(1) == {"research_project_id": RESEARCH}
    assert built.structuredContent["evidence_packet"]["conflicts"][0]["claim_id"] == "retry-key"
    assert (
        shown.structuredContent["claim_evidence"]["contradicting_evidence"][0]["citation"][
            "relation"
        ]
        == "contradicts"
    )
    assert "without selecting a side" in str(built.content[0].text)


@pytest.mark.asyncio
async def test_evidence_mcp_refuses_unknown_fields_and_unbounded_budget() -> None:
    api = RecordedApi()
    invalid = await call(
        api,
        "build_evidence_packet",
        {
            "query": "retry idempotency",
            "research_project_id": RESEARCH,
            "token_budget": 16001,
            "command": "not accepted",
        },
    )

    assert invalid.isError is True
    assert "invalid arguments" in str(invalid.content[0].text)
    assert api.requests == []
