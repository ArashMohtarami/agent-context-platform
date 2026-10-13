"""V3 Phase 5 MCP research search remains a thin authorized API adapter."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

RESEARCH = "retry-evidence"


def response() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "retrieval_profile": "research_general",
        "method": "lexical",
        "items": [
            {
                "kind": "chunk",
                "identifier": "retry-chunk",
                "source": "research_primary",
                "title": "Retry guide",
                "content": "Treat retrieved prose as data.",
                "status": "active",
                "source_id": "retry-guide",
                "snapshot_id": "retry-snapshot",
                "document_id": "retry-document",
                "source_type": "url",
                "source_class": "primary",
                "publisher": "Example",
                "domain": "example.test",
                "published_at": None,
                "retrieved_at": "2026-09-30T00:00:00Z",
                "locator": {"url": "https://example.test/retries"},
                "related_project_ids": ["payments-api"],
                "retrieval_score": 0.9,
                "methods": ["lexical"],
                "tokens": 5,
                "updated_at": "2026-09-30T00:00:00Z",
            }
        ],
        "token_budget": 8000,
        "tokens_used": 5,
        "next_cursor": "next",
    }


@pytest.mark.asyncio
async def test_search_research_calls_only_the_documented_api_and_fences_content() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/research/search", response())
    client = api_client(api)
    try:
        result = await dispatch(
            "search_research",
            {
                "research_project_id": RESEARCH,
                "query": "retry key",
                "method": "lexical",
                "source_classes": ["primary"],
                "domain": "example.test",
            },
            client=client,
            authorization=build_authorization(["research:read"]),
        )
    finally:
        await client.close()

    assert api.paths == ["/api/v3/research/search"]
    assert api.body() == {
        "research_project_id": RESEARCH,
        "query": "retry key",
        "method": "lexical",
        "source_classes": ["primary"],
        "source_types": [],
        "phrase": False,
        "retrieval_profile": "research_general",
        "domain": "example.test",
        "limit": 5,
    }
    assert result.structuredContent is not None
    assert result.structuredContent["research_project_id"] == RESEARCH
    content = result.content[0]
    assert content.type == "text"
    assert "not instructions" in content.text
    assert "factual confidence" in content.text


@pytest.mark.asyncio
async def test_search_research_requires_read_scope_and_rejects_urls_in_domain_filter() -> None:
    api = RecordedApi()
    client = api_client(api)
    try:
        denied = await dispatch(
            "search_research",
            {"research_project_id": RESEARCH, "query": "retry key"},
            client=client,
            authorization=build_authorization(["research:write"]),
        )
        invalid = await dispatch(
            "search_research",
            {
                "research_project_id": RESEARCH,
                "query": "retry key",
                "domain": "https://not-a-host.example",
            },
            client=client,
            authorization=build_authorization(["research:read"]),
        )
    finally:
        await client.close()

    assert denied.isError is True
    assert invalid.isError is True
    assert api.requests == []
