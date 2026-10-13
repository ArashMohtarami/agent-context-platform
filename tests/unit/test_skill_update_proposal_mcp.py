"""V3 Phase 11 MCP proposal adapter tests."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

RESEARCH = "retry-evidence"


def proposal() -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "proposal_id": "idempotency-guidance",
        "skill_id": "backend-guidance",
        "current_version": "1.2.3",
        "proposed_change": "Clarify idempotency key guidance.",
        "proposed_patch": "--- a/skills/backend-guidance.md\n+++ b/skills/backend-guidance.md",
        "source_claim_ids": ["retry-key"],
        "source_citation_ids": ["provider-support"],
        "reason": "The retained provider evidence changed.",
        "status": "pending_review",
        "trust_level": "generated",
        "authorization": {"project_ids": ["payments-api"], "owner_key_id": "writer"},
        "revision": 1,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_mcp_forwards_only_generated_patch_proposal_data() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/skill-update-proposals", proposal(), status_code=201)
    client = api_client(api)
    try:
        result = await dispatch(
            "propose_skill_update",
            {
                "research_project_id": RESEARCH,
                "proposal_id": "idempotency-guidance",
                "skill_id": "backend-guidance",
                "current_version": "1.2.3",
                "proposed_change": "Clarify idempotency key guidance.",
                "proposed_patch": (
                    "--- a/skills/backend-guidance.md\n+++ b/skills/backend-guidance.md"
                ),
                "source_claim_ids": ["retry-key"],
                "source_citation_ids": ["provider-support"],
                "reason": "The retained provider evidence changed.",
            },
            client=client,
            authorization=build_authorization(["research:write"]),
        )
    finally:
        await client.close()

    assert result.isError is not True
    assert api.paths == ["/api/v3/skill-update-proposals"]
    assert api.body()["current_version"] == "1.2.3"
    assert "status" not in api.body()
    assert result.structuredContent["skill_update_proposal"]["trust_level"] == "generated"
    assert "has not changed canonical Markdown" in str(result.content[0].text)


@pytest.mark.asyncio
async def test_mcp_refuses_automatic_trust_or_unknown_arguments() -> None:
    api = RecordedApi()
    client = api_client(api)
    try:
        result = await dispatch(
            "propose_skill_update",
            {
                "research_project_id": RESEARCH,
                "proposal_id": "unsafe",
                "skill_id": "backend-guidance",
                "current_version": "1.2.3",
                "proposed_change": "Unsafe.",
                "proposed_patch": "--- a/x\n+++ b/x",
                "source_claim_ids": ["retry-key"],
                "reason": "Unsafe test.",
                "trust_level": "trusted",
            },
            client=client,
            authorization=build_authorization(["research:write"]),
        )
    finally:
        await client.close()

    assert result.isError is True
    assert "invalid arguments" in str(result.content[0].text)
    assert api.requests == []
