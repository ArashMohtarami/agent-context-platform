"""Phase 7 conflict contracts shared by the API and MCP transports."""

import pytest

from app.concurrency import REVISION_CONFLICT_CODE, REVISION_CONFLICT_MESSAGE
from app.mcp.auth import Authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client


@pytest.mark.asyncio
async def test_mcp_translates_the_standard_revision_conflict_without_leaking_details() -> None:
    """Agents receive a machine-readable conflict plus a safe recovery hint."""
    api = RecordedApi()
    memory_id = "6720a1b2c3d4e5f60718293b"
    api.respond(
        "PATCH",
        f"/api/v1/memory/{memory_id}",
        {
            "error": {
                "code": REVISION_CONFLICT_CODE,
                "message": REVISION_CONFLICT_MESSAGE,
            }
        },
        status_code=409,
    )
    client = api_client(api)
    try:
        result = await dispatch(
            "update_memory",
            {
                "memory_id": memory_id,
                "content": "Codex's proposed update",
                "expected_revision": 5,
            },
            client=client,
            authorization=Authorization(),
        )
    finally:
        await client.close()

    assert result.isError is True
    assert result.structuredContent == {
        "error": {
            "code": REVISION_CONFLICT_CODE,
            "message": REVISION_CONFLICT_MESSAGE,
        }
    }
    assert "Reload the resource" in str(result.content[0].text)
    assert api.paths == [f"/api/v1/memory/{memory_id}"]
    assert api.body()["expected_revision"] == 5
