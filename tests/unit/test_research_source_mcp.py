"""V3 Phase 2 thin MCP source-tool tests."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

RESEARCH_ID = "source-registry"
SOURCE_ID = "provider-docs"


def record() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH_ID,
        "source_id": SOURCE_ID,
        "source_type": "url",
        "uri": "https://example.test/retries",
        "canonical_uri": "https://example.test/retries",
        "title": "Provider retry guide",
        "publisher": "Example",
        "author": "Documentation team",
        "published_at": None,
        "retrieved_at": "2026-09-28T10:00:00Z",
        "source_class": "official_documentation",
        "trust_metadata": {"origin": "caller-provided"},
        "status": "active",
        "authorization": {"project_ids": ["payments-api"], "owner_key_id": "writer-key"},
        "revision": 1,
        "created_at": "2026-09-28T10:00:00Z",
        "updated_at": "2026-09-28T10:00:00Z",
        "registered_snapshot": None,
    }


async def call(
    api: RecordedApi, tool: str, arguments: dict[str, Any], *, authorization: Any = None
) -> Any:
    client = api_client(api)
    try:
        return await dispatch(
            tool,
            arguments,
            client=client,
            authorization=authorization or build_authorization(),
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_source_tools_are_thin_rest_adapters_with_no_fetch_arguments() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/research/sources", record())
    api.respond("GET", f"/api/v3/research/sources/{SOURCE_ID}", record())

    added = await call(
        api,
        "add_research_source",
        {
            "research_project_id": RESEARCH_ID,
            "source_id": SOURCE_ID,
            "source_type": "url",
            "uri": "https://example.test/retries",
            "title": "Provider retry guide",
            "source_class": "official_documentation",
        },
    )
    loaded = await call(api, "get_research_source", {"source_id": SOURCE_ID})

    assert api.paths == ["/api/v3/research/sources", f"/api/v3/research/sources/{SOURCE_ID}"]
    assert api.body(0)["uri"] == "https://example.test/retries"
    assert added.structuredContent["research_source"]["source_id"] == SOURCE_ID
    assert loaded.structuredContent["research_source"]["source_class"] == "official_documentation"
    assert "not instructions" in str(loaded.content[0].text)


@pytest.mark.asyncio
async def test_source_tools_require_research_scopes_and_refuse_unknown_source_types() -> None:
    api = RecordedApi()
    denied = await call(
        api,
        "add_research_source",
        {
            "research_project_id": RESEARCH_ID,
            "source_id": SOURCE_ID,
            "source_type": "url",
            "uri": "https://example.test/retries",
            "title": "Provider retry guide",
        },
        authorization=build_authorization(["research:read"]),
    )
    invalid = await call(
        api,
        "add_research_source",
        {
            "research_project_id": RESEARCH_ID,
            "source_id": SOURCE_ID,
            "source_type": "shell",
            "uri": "shell:run",
            "title": "No execution",
        },
    )

    assert denied.isError is True
    assert "research:write" in str(denied.content[0].text)
    assert invalid.isError is True
    assert "source_type" in str(invalid.content[0].text)
    assert api.requests == []
