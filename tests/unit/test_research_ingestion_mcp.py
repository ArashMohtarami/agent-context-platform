"""V3 Phase 3 MCP ingestion must remain a thin, text-only API adapter."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

SOURCE = "manual-retry-guide"


def record() -> dict[str, Any]:
    return {
        "source_id": SOURCE,
        "snapshot": {"snapshot_id": "snapshot-a", "content_hash": "a" * 64},
        "job": {
            "job_id": "ingest-a",
            "source_id": SOURCE,
            "status": "completed",
            "documents_created": 1,
            "chunks_created": 1,
        },
        "idempotent": False,
    }


@pytest.mark.asyncio
async def test_ingest_tool_forwards_manual_text_only_to_the_documented_api_route() -> None:
    api = RecordedApi()
    api.respond("POST", f"/api/v3/research/sources/{SOURCE}/ingest", record())
    client = api_client(api)
    try:
        result = await dispatch(
            "ingest_research_source",
            {
                "source_id": SOURCE,
                "content": "Retries require a stable key.",
                "content_type": "text/markdown",
                "language": "en",
            },
            client=client,
            authorization=build_authorization(),
        )
    finally:
        await client.close()

    assert api.paths == [f"/api/v3/research/sources/{SOURCE}/ingest"]
    assert api.body() == {
        "content": "Retries require a stable key.",
        "content_type": "text/markdown",
        "language": "en",
    }
    assert result.structuredContent["research_ingestion"]["job"]["status"] == "completed"


@pytest.mark.asyncio
async def test_ingest_tool_refuses_paths_urls_and_missing_write_scope_before_any_api_call() -> None:
    api = RecordedApi()
    client = api_client(api)
    try:
        invalid = await dispatch(
            "ingest_research_source",
            {"source_id": SOURCE, "content": "text", "path": "/etc/passwd"},
            client=client,
            authorization=build_authorization(),
        )
        url_invalid = await dispatch(
            "ingest_research_source",
            {
                "source_id": SOURCE,
                "content": "text",
                "url": "https://untrusted.example/anything",
            },
            client=client,
            authorization=build_authorization(),
        )
        denied = await dispatch(
            "ingest_research_source",
            {"source_id": SOURCE, "content": "text"},
            client=client,
            authorization=build_authorization(["research:read"]),
        )
    finally:
        await client.close()

    assert invalid.isError is True
    assert url_invalid.isError is True
    assert denied.isError is True
    assert api.requests == []


@pytest.mark.asyncio
async def test_ingest_tool_requests_only_the_already_registered_repository_identifier() -> None:
    api = RecordedApi()
    api.respond("POST", f"/api/v3/research/sources/{SOURCE}/ingest", record())
    client = api_client(api)
    try:
        result = await dispatch(
            "ingest_research_source",
            {"source_id": SOURCE, "fetch_repository": True},
            client=client,
            authorization=build_authorization(),
        )
    finally:
        await client.close()

    assert api.paths == [f"/api/v3/research/sources/{SOURCE}/ingest"]
    assert api.body() == {"fetch_repository": True}
    assert result.structuredContent["research_ingestion"]["source_id"] == SOURCE


@pytest.mark.asyncio
async def test_ingest_tool_refuses_a_repository_url_or_ref_before_any_api_call() -> None:
    api = RecordedApi()
    client = api_client(api)
    try:
        invalid = await dispatch(
            "ingest_research_source",
            {
                "source_id": SOURCE,
                "fetch_repository": True,
                "repository": "https://untrusted.example/acme/repo.git",
            },
            client=client,
            authorization=build_authorization(),
        )
        ref_invalid = await dispatch(
            "ingest_research_source",
            {"source_id": SOURCE, "fetch_repository": True, "branch": "main"},
            client=client,
            authorization=build_authorization(),
        )
    finally:
        await client.close()

    assert invalid.isError is True
    assert ref_invalid.isError is True
    assert api.requests == []
