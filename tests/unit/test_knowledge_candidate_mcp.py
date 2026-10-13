"""V3 Phase 9 MCP candidate adapters are thin, review-gated REST clients."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

RESEARCH = "retry-evidence"
PROJECT = "payments-api"


def candidate(status: str = "pending_review") -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "candidate_id": "retry-context",
        "candidate_type": "project_context",
        "target_project_id": PROJECT,
        "target_skill_id": None,
        "current_skill_version": None,
        "title": "Retry idempotency requirement",
        "content": "Use an idempotency key for retryable writes.",
        "source_claim_ids": ["retry-key"],
        "source_citation_ids": ["provider-support"],
        "confidence": 0.9,
        "confidence_rationale": "Explicit reviewer assessment.",
        "status": status,
        "reviewer": "reviewer-key" if status != "pending_review" else None,
        "reviewed_at": "2026-10-01T12:00:00Z" if status != "pending_review" else None,
        "review_notes": "Evidence reviewed." if status != "pending_review" else None,
        "authorization": {"project_ids": [PROJECT], "owner_key_id": "writer-key"},
        "revision": 2 if status != "pending_review" else 1,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T12:00:00Z",
    }


def event(decision: str) -> dict[str, Any]:
    return {
        "research_project_id": RESEARCH,
        "candidate_id": "retry-context",
        "event_id": "review-abcdefghijklmnopqrstuvwx123456",
        "target_type": "project_context",
        "target_id": PROJECT,
        "reviewer": "reviewer-key",
        "decision": decision,
        "decided_at": "2026-10-01T12:00:00Z",
        "reason": "Evidence reviewed.",
        "authorization": {"project_ids": [PROJECT], "owner_key_id": "reviewer-key"},
        "created_at": "2026-10-01T12:00:00Z",
    }


@pytest.mark.asyncio
async def test_candidate_mcp_forwards_only_proposal_and_explicit_review_requests() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v3/knowledge-candidates", candidate(), status_code=201)
    api.respond(
        "POST",
        "/api/v3/knowledge-candidates/retry-context/approve",
        {"candidate": candidate("approved"), "event": event("approved")},
    )
    client = api_client(api)
    try:
        proposed = await dispatch(
            "propose_knowledge_candidate",
            {
                "research_project_id": RESEARCH,
                "candidate_id": "retry-context",
                "candidate_type": "project_context",
                "target_project_id": PROJECT,
                "title": "Retry idempotency requirement",
                "content": "Use an idempotency key for retryable writes.",
                "source_claim_ids": ["retry-key"],
                "source_citation_ids": ["provider-support"],
                "confidence": 0.9,
            },
            client=client,
            authorization=build_authorization(["research:write"]),
        )
        approved = await dispatch(
            "approve_knowledge_candidate",
            {
                "research_project_id": RESEARCH,
                "candidate_id": "retry-context",
                "expected_revision": 1,
                "reason": "Evidence reviewed.",
            },
            client=client,
            authorization=build_authorization(["research:approve"]),
        )
    finally:
        await client.close()

    assert proposed.isError is not True
    assert approved.isError is not True
    assert api.paths == [
        "/api/v3/knowledge-candidates",
        "/api/v3/knowledge-candidates/retry-context/approve",
    ]
    assert "status" not in api.body(0)
    assert "pending review" in str(proposed.content[0].text)
    assert approved.structuredContent["promotion_event"]["decision"] == "approved"
    assert "no V1 target" in str(approved.content[0].text)


@pytest.mark.asyncio
async def test_candidate_mcp_lists_and_rejects_without_an_unbounded_operation() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v3/knowledge-candidates",
        {"items": [candidate()], "next_cursor": None},
    )
    api.respond(
        "POST",
        "/api/v3/knowledge-candidates/retry-context/reject",
        {"candidate": candidate("needs_changes"), "event": event("needs_changes")},
    )
    client = api_client(api)
    try:
        listed = await dispatch(
            "list_knowledge_candidates",
            {"research_project_id": RESEARCH, "status": "pending_review", "limit": 3},
            client=client,
            authorization=build_authorization(["research:read"]),
        )
        rejected = await dispatch(
            "reject_knowledge_candidate",
            {
                "research_project_id": RESEARCH,
                "candidate_id": "retry-context",
                "expected_revision": 1,
                "decision": "needs_changes",
            },
            client=client,
            authorization=build_authorization(["research:write"]),
        )
    finally:
        await client.close()

    assert listed.isError is not True
    assert api.query(0) == {
        "research_project_id": RESEARCH,
        "status": "pending_review",
        "limit": "3",
    }
    assert rejected.structuredContent["knowledge_candidate"]["status"] == "needs_changes"
    assert api.body(1)["decision"] == "needs_changes"


@pytest.mark.asyncio
async def test_candidate_mcp_refuses_automatic_status_and_unknown_arguments() -> None:
    api = RecordedApi()
    client = api_client(api)
    try:
        result = await dispatch(
            "propose_knowledge_candidate",
            {
                "research_project_id": RESEARCH,
                "candidate_id": "unsafe",
                "candidate_type": "memory",
                "title": "Unsafe",
                "content": "No automatic promotion.",
                "source_claim_ids": ["retry-key"],
                "status": "approved",
            },
            client=client,
            authorization=build_authorization(["research:write"]),
        )
    finally:
        await client.close()

    assert result.isError is True
    assert "invalid arguments" in str(result.content[0].text)
    assert api.requests == []


@pytest.mark.asyncio
async def test_candidate_mcp_forwards_one_explicit_controlled_promotion() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v3/knowledge-candidates/retry-context/promote",
        {
            "candidate": candidate("promoted"),
            "event": event("promoted"),
            "target": {
                "target_type": "project_context",
                "target_id": "0123456789abcdef01234567",
                "provenance": {
                    "candidate_id": "retry-context",
                    "research_project_id": RESEARCH,
                    "claim_ids": ["retry-key"],
                    "citation_ids": ["provider-support"],
                    "source_ids": ["provider-docs"],
                    "verified_at": "2026-10-01T12:00:00Z",
                },
            },
        },
    )
    client = api_client(api)
    try:
        promoted = await dispatch(
            "promote_knowledge_candidate",
            {
                "research_project_id": RESEARCH,
                "candidate_id": "retry-context",
                "expected_revision": 2,
            },
            client=client,
            authorization=build_authorization(["research:promote"]),
        )
    finally:
        await client.close()

    assert promoted.isError is not True
    assert api.paths == ["/api/v3/knowledge-candidates/retry-context/promote"]
    assert api.body() == {"research_project_id": RESEARCH, "expected_revision": 2}
    assert promoted.structuredContent["promoted_target"]["provenance"]["source_ids"] == [
        "provider-docs"
    ]
    assert "did not modify skills" in str(promoted.content[0].text)


@pytest.mark.asyncio
async def test_candidate_mcp_requires_dedicated_approval_and_promotion_scopes() -> None:
    """A research writer may propose, but cannot approve or promote its own work."""
    api = RecordedApi()
    client = api_client(api)
    try:
        approval = await dispatch(
            "approve_knowledge_candidate",
            {
                "research_project_id": RESEARCH,
                "candidate_id": "retry-context",
                "expected_revision": 1,
            },
            client=client,
            authorization=build_authorization(["research:write"]),
        )
        promotion = await dispatch(
            "promote_knowledge_candidate",
            {
                "research_project_id": RESEARCH,
                "candidate_id": "retry-context",
                "expected_revision": 2,
            },
            client=client,
            authorization=build_authorization(["research:write", "context:write"]),
        )
    finally:
        await client.close()

    assert approval.isError is True
    assert promotion.isError is True
    assert approval.structuredContent == {
        "error": {
            "code": "FORBIDDEN",
            "message": "this caller is not authorized for research:approve",
        }
    }
    assert promotion.structuredContent == {
        "error": {
            "code": "FORBIDDEN",
            "message": "this caller is not authorized for research:promote",
        }
    }
    assert api.requests == []
