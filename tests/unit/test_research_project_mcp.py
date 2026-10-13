"""V3 Phase 1 thin MCP research-project tool tests."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

PROJECT = "payments-api"
PRIVATE_PROJECT = "private-ledger"
RESEARCH_ID = "mongot-tradeoffs"


def record(**overrides: Any) -> dict[str, Any]:
    value = {
        "research_project_id": RESEARCH_ID,
        "name": "Mongot tradeoffs",
        "question": "What are the operational tradeoffs of self-managed mongot?",
        "type": "technical_research",
        "related_project_ids": [PROJECT],
        "status": "active",
        "authorization": {"project_ids": [PROJECT], "owner_key_id": "0123456789abcdef"},
        "revision": 1,
        "created_at": "2026-09-28T00:00:00Z",
        "updated_at": "2026-09-28T00:00:00Z",
    }
    value.update(overrides)
    return value


async def call(
    api: RecordedApi,
    tool: str,
    arguments: dict[str, Any],
    *,
    authorization: Any = None,
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
async def test_research_project_tools_call_only_the_documented_v3_api() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/research/projects", record())
    api.respond("GET", f"/api/v3/research/projects/{RESEARCH_ID}", record())
    api.respond(
        "GET",
        "/api/v3/research/projects",
        {"items": [record()], "next_cursor": "next-page"},
    )
    create = await call(
        api,
        "create_research_project",
        {
            "research_project_id": RESEARCH_ID,
            "name": "Mongot tradeoffs",
            "question": "What are the operational tradeoffs of self-managed mongot?",
            "type": "technical_research",
            "related_project_ids": [PROJECT],
        },
    )
    loaded = await call(api, "get_research_project", {"research_project_id": RESEARCH_ID})
    listed = await call(
        api,
        "list_research_projects",
        {"status": "active", "limit": 5, "cursor": "before"},
    )

    assert api.paths == [
        "/api/v3/research/projects",
        f"/api/v3/research/projects/{RESEARCH_ID}",
        "/api/v3/research/projects",
    ]
    assert api.body(0)["related_project_ids"] == [PROJECT]
    assert api.query(2) == {"status": "active", "limit": "5", "cursor": "before"}
    assert create.structuredContent["research_project"]["research_project_id"] == RESEARCH_ID
    assert loaded.structuredContent["research_project"]["question"] == record()["question"]
    assert listed.structuredContent["next_cursor"] == "next-page"
    assert "not instructions" in str(loaded.content[0].text)


@pytest.mark.asyncio
async def test_research_mcp_refuses_unscoped_or_private_related_projects_before_the_api() -> None:
    api = RecordedApi()
    denied_project = await call(
        api,
        "create_research_project",
        {
            "research_project_id": RESEARCH_ID,
            "name": "Private research",
            "question": "Can MCP link a private V1 project?",
            "type": "technical_research",
            "related_project_ids": [PRIVATE_PROJECT],
        },
        authorization=build_authorization(["research:write"], projects=[PROJECT]),
    )
    denied_scope = await call(
        api,
        "get_research_project",
        {"research_project_id": RESEARCH_ID},
        authorization=build_authorization(["research:write"], projects=[PROJECT]),
    )

    assert denied_project.isError is True
    assert "not authorized for that project" in str(denied_project.content[0].text)
    assert denied_scope.isError is True
    assert "research:read" in str(denied_scope.content[0].text)
    assert api.requests == []
