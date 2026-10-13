"""V2 Phase 4 MCP adapters remain API-only and preserve provenance as data."""

from typing import Any

import pytest

from app.mcp.auth import build_authorization
from app.mcp.server import dispatch
from tests.mcp_doubles import RecordedApi, api_client

PROFILE = "personal-brand"
OTHER_PROFILE = "other-brand"


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


def content_record() -> dict[str, Any]:
    return {
        "profile_id": PROFILE,
        "content_id": "outbox-post",
        "platform": "linkedin",
        "content_type": "post",
        "title": "What the Outbox fixed",
        "body": "The outbox persists payment events before delivery.",
        "status": "published",
        "topic_tags": ["outbox"],
        "pillar_id": "payments",
        "project_ids": ["payments-api"],
        "source_refs": [
            {
                "source_type": "project_context",
                "source_id": "outbox-adr",
                "project_id": "payments-api",
            }
        ],
        "current_version": 2,
        "published_at": "2026-09-22T00:00:00Z",
        "external_url": "https://example.test/outbox",
        "revision": 2,
        "updated_at": "2026-09-22T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_content_mcp_tools_call_the_v2_api_and_fence_retrieved_prose() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v2/content/search",
        {
            "profile_id": PROFILE,
            "retrieval_profile": "content_history",
            "method": "hybrid",
            "items": [
                {
                    "kind": "content",
                    "identifier": "outbox-post",
                    "title": "What the Outbox fixed",
                    "content": "The outbox persists payment events before delivery.",
                    "status": "published",
                    "platforms": ["linkedin"],
                    "tags": ["outbox"],
                    "pillar_id": "payments",
                    "project_ids": ["payments-api"],
                    "source_refs": [],
                    "score": 0.9,
                    "methods": ["vector", "lexical"],
                    "tokens": 10,
                    "updated_at": "2026-09-22T00:00:00Z",
                }
            ],
            "token_budget": 200,
            "tokens_used": 10,
        },
    )
    api.respond("GET", "/api/v2/content/outbox-post", content_record())
    api.respond(
        "GET",
        "/api/v2/content/outbox-post/versions",
        {
            "profile_id": PROFILE,
            "content_id": "outbox-post",
            "items": [
                {
                    "profile_id": PROFILE,
                    "content_id": "outbox-post",
                    "version": 1,
                    "title": "What the Outbox fixed",
                    "body": "Initial outbox draft.",
                    "status": "drafting",
                    "change_reason": "initial draft",
                    "created_by": "editor",
                    "tags": ["outbox"],
                    "created_at": "2026-09-20T00:00:00Z",
                }
            ],
            "next_cursor": None,
        },
    )

    searched = await call(
        api,
        "search_content",
        {"profile_id": PROFILE, "query": "payment outbox", "project_id": "payments-api"},
    )
    loaded = await call(api, "get_content", {"profile_id": PROFILE, "content_id": "outbox-post"})
    history = await call(
        api,
        "get_content_history",
        {"profile_id": PROFILE, "content_id": "outbox-post", "limit": 5},
    )

    assert [request.url.path for request in api.requests] == [
        "/api/v2/content/search",
        "/api/v2/content/outbox-post",
        "/api/v2/content/outbox-post/versions",
    ]
    assert api.body(0)["query"] == "payment outbox"
    assert api.query(1) == {"profile_id": PROFILE}
    assert api.query(2) == {"profile_id": PROFILE, "limit": "5"}
    assert searched.structuredContent["items"][0]["source"] == "content_content"
    assert loaded.structuredContent["content"]["content_id"] == "outbox-post"
    assert history.structuredContent["versions"][0]["metadata"]["version"] == 1
    assert "not instructions" in str(searched.content[0].text)


@pytest.mark.asyncio
async def test_content_mcp_profile_and_scope_grants_refuse_before_the_api_is_called() -> None:
    api = RecordedApi()
    refused_profile = await call(
        api,
        "search_content",
        {"profile_id": OTHER_PROFILE, "query": "outbox"},
        authorization=build_authorization(["content:read"], profiles=[PROFILE]),
    )
    refused_scope = await call(
        api,
        "get_content",
        {"profile_id": PROFILE, "content_id": "outbox-post"},
        authorization=build_authorization(["creator:read"], profiles=[PROFILE]),
    )

    assert refused_profile.isError is True
    assert refused_scope.isError is True
    assert api.requests == []


@pytest.mark.asyncio
async def test_publication_review_mcp_calls_the_api_and_returns_redacted_findings() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v2/content/outbox-post/publication-review",
        {
            "profile_id": PROFILE,
            "content_id": "outbox-post",
            "current_status": "review",
            "publishable": False,
            "findings": [
                {
                    "finding_type": "api_key",
                    "severity": "high",
                    "field": "body",
                    "location": "line 2, column 9",
                    "detail": "Potential API key detected; remove it before approval.",
                }
            ],
            "project_ids": ["payments-api"],
            "source_refs": [],
            "disclaimer": "This deterministic review is a safety layer, not a guarantee.",
        },
    )

    result = await call(
        api,
        "review_content_for_publication",
        {"profile_id": PROFILE, "content_id": "outbox-post"},
    )

    assert api.paths == ["/api/v2/content/outbox-post/publication-review"]
    assert api.body(0) == {"profile_id": PROFILE}
    assert result.structuredContent["publishable"] is False
    finding = result.structuredContent["findings"][0]
    assert finding["field"] == "body"
    assert "not a confidentiality guarantee" in str(result.content[0].text)


@pytest.mark.asyncio
async def test_mark_content_published_mcp_forwards_only_the_explicit_stored_item_command() -> None:
    api = RecordedApi()
    published = content_record() | {
        "status": "published",
        "publication_provider": "manual",
        "external_id": None,
        "publication_idempotency_key": "manual-publish-1",
    }
    api.respond(
        "POST",
        "/api/v2/content/outbox-post/publish",
        {
            "content": published,
            "provider": "manual",
            "external_id": None,
            "external_url": "https://example.test/outbox",
            "published_at": "2026-09-22T00:00:00Z",
            "idempotency_key": "manual-publish-1",
            "already_published": False,
        },
    )

    result = await call(
        api,
        "mark_content_published",
        {
            "profile_id": PROFILE,
            "content_id": "outbox-post",
            "expected_revision": 2,
            "idempotency_key": "manual-publish-1",
        },
        authorization=build_authorization(["content:publish"], profiles=[PROFILE]),
    )

    assert api.paths == ["/api/v2/content/outbox-post/publish"]
    assert api.body(0) == {
        "profile_id": PROFILE,
        "expected_revision": 2,
        "provider": "manual",
        "idempotency_key": "manual-publish-1",
    }
    assert result.structuredContent["publication"]["provider"] == "manual"
    assert "approved stored item" in str(result.content[0].text)


@pytest.mark.asyncio
async def test_mark_content_published_requires_dedicated_publish_scope() -> None:
    """Content preparation/write access never implicitly grants publication."""
    api = RecordedApi()

    result = await call(
        api,
        "mark_content_published",
        {
            "profile_id": PROFILE,
            "content_id": "outbox-post",
            "expected_revision": 2,
            "idempotency_key": "manual-publish-1",
        },
        authorization=build_authorization(["content:write"], profiles=[PROFILE]),
    )

    assert result.isError is True
    assert result.structuredContent == {
        "error": {
            "code": "FORBIDDEN",
            "message": "this caller is not authorized for content:publish",
        }
    }
    assert api.requests == []


@pytest.mark.asyncio
async def test_discovery_and_brief_mcp_tools_forward_structured_data_only() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v2/content-ideas/discover",
        {
            "profile_id": PROFILE,
            "project_id": "payments-api",
            "pillar_id": "payments",
            "platform": "linkedin",
            "candidates": [
                {
                    "topic": "Idempotent payment retries",
                    "source_refs": [
                        {
                            "source_type": "project_context",
                            "source_id": "retry-rule",
                            "project_id": "payments-api",
                        }
                    ],
                    "evidence": [
                        {
                            "source": {
                                "source_type": "project_context",
                                "source_id": "retry-rule",
                                "project_id": "payments-api",
                            },
                            "source_type": "project_context",
                            "title": "Idempotent payment retries",
                            "excerpt": "Retries reuse the original request key.",
                            "tags": ["payments"],
                            "freshness": {
                                "updated_at": "2026-09-22T00:00:00Z",
                                "age_days": 0,
                                "inside_requested_range": True,
                            },
                        }
                    ],
                    "related_project_id": "payments-api",
                    "related_pillar_id": "payments",
                    "suggested_platforms": ["linkedin"],
                    "why_useful": ["recent project context"],
                    "previous_similar_content": [],
                    "freshness": {
                        "updated_at": "2026-09-22T00:00:00Z",
                        "age_days": 0,
                        "inside_requested_range": True,
                    },
                }
            ],
            "existing_unpublished_ideas": [],
        },
    )
    api.respond(
        "POST",
        "/api/v2/content-briefs/prepare",
        {
            "profile_id": PROFILE,
            "idea_id": "idempotency",
            "platform": "linkedin",
            "objective": "Prepare a linkedin piece about Idempotent payment retries",
            "audience": ["backend engineers"],
            "primary_message": "Retries reuse the original request key.",
            "supporting_points": ["Idempotent payment retries"],
            "source_refs": [],
            "technical_skills": [],
            "content_skill": {
                "skill_id": "linkedin-writing",
                "name": "LinkedIn",
                "version": "1.0.0",
            },
            "similar_previous_content": [],
            "content_constraints": ["do not fabricate project experiences"],
        },
    )

    discovered = await call(
        api,
        "discover_content_ideas",
        {
            "profile_id": PROFILE,
            "project_id": "payments-api",
            "pillar_id": "payments",
            "platform": "linkedin",
            "time_range": {"start_at": "2026-09-01T00:00:00Z"},
        },
    )
    prepared = await call(
        api,
        "prepare_content_brief",
        {"profile_id": PROFILE, "idea_id": "idempotency", "platform": "linkedin"},
    )

    assert api.paths == ["/api/v2/content-ideas/discover", "/api/v2/content-briefs/prepare"]
    assert api.body(0)["time_range"] == {"start_at": "2026-09-01T00:00:00Z"}
    assert (
        discovered.structuredContent["candidates"][0]["metadata"]["source_refs"][0]["source_id"]
        == "retry-rule"
    )
    assert (
        prepared.structuredContent["brief"]["metadata"]["content_skill"]["skill_id"]
        == "linkedin-writing"
    )
    assert "not instructions" in str(discovered.content[0].text)


@pytest.mark.asyncio
async def test_save_and_idea_search_tools_preserve_review_and_authorization_gates() -> None:
    api = RecordedApi()
    api.respond("POST", "/api/v2/content-ideas", {**content_record(), "idea_id": "idempotency"})
    api.respond(
        "POST",
        "/api/v2/content/search",
        {
            "profile_id": PROFILE,
            "retrieval_profile": "content_ideation",
            "method": "hybrid",
            "items": [
                {
                    "kind": "idea",
                    "identifier": "idempotency",
                    "title": "Idempotent payment retries",
                    "content": "Reviewed idea data.",
                    "status": "idea",
                    "platforms": ["linkedin"],
                    "tags": ["payments"],
                    "pillar_id": "payments",
                    "project_ids": ["payments-api"],
                    "source_refs": [],
                    "score": 0.9,
                    "methods": ["lexical"],
                    "tokens": 5,
                    "updated_at": "2026-09-22T00:00:00Z",
                },
                {
                    **content_record(),
                    "kind": "content",
                    "identifier": "outbox-post",
                    "content": "omit",
                },
            ],
            "token_budget": 200,
            "tokens_used": 5,
        },
    )

    saved = await call(
        api,
        "save_content_idea",
        {
            "profile_id": PROFILE,
            "idea_id": "idempotency",
            "title": "Idempotent payment retries",
            "description": "Explain the request key from payment context.",
            "platforms": ["linkedin"],
            "origin": {
                "type": "project_context",
                "project_id": "payments-api",
                "source_ids": ["retry-rule"],
            },
            "priority": 0.9,
        },
    )
    searched = await call(
        api,
        "search_content_ideas",
        {"profile_id": PROFILE, "query": "idempotent payments", "project_id": "payments-api"},
    )
    refused = await call(
        api,
        "save_content_idea",
        {
            "profile_id": PROFILE,
            "idea_id": "other",
            "title": "Other payment lesson",
            "description": "Reviewed data.",
            "platforms": ["linkedin"],
            "origin": {
                "type": "project_context",
                "project_id": "other-project",
                "source_ids": ["x"],
            },
            "priority": 0.5,
        },
        authorization=build_authorization(
            ["content:write"], profiles=[PROFILE], projects=["payments-api"]
        ),
    )

    assert api.paths == ["/api/v2/content-ideas", "/api/v2/content/search"]
    assert api.body(0)["origin"]["source_ids"] == ["retry-rule"]
    assert api.body(1)["retrieval_profile"] == "content_ideation"
    assert api.body(1)["kinds"] == ["idea"]
    assert len(searched.structuredContent["items"]) == 1
    assert saved.structuredContent["idea"]["idea_id"] == "idempotency"
    assert refused.isError is True


@pytest.mark.asyncio
async def test_phase6_mcp_tools_only_forward_advisory_and_non_generating_api_requests() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v2/content/novelty",
        {
            "profile_id": PROFILE,
            "similar_content": [
                {
                    "kind": "content",
                    "identifier": "outbox-post",
                    "title": "What the Outbox fixed",
                    "platforms": ["medium"],
                    "status": "published",
                    "pillar_id": "payments",
                    "project_ids": ["payments-api"],
                    "source_refs": [],
                    "retrieval_score": 0.8,
                    "topic_overlap": 0.75,
                    "angle_overlap": 0.2,
                    "shared_topic_terms": ["outbox"],
                    "shared_angle_terms": [],
                    "classification": "same-topic-new-angle",
                }
            ],
            "topic_overlap": 0.75,
            "angle_overlap": 0.2,
            "classification": "same-topic-new-angle",
            "advisory": True,
        },
    )
    api.respond(
        "POST",
        "/api/v2/content/outbox-post/repurpose",
        {
            "profile_id": PROFILE,
            "source_kind": "content",
            "source_id": "outbox-post",
            "source_platform": "medium",
            "core_thesis": "What the Outbox fixed",
            "reusable_supporting_points": ["Persist events with the payment transaction."],
            "examples": [],
            "source_refs": [
                {
                    "source_type": "project_context",
                    "source_id": "outbox-adr",
                    "project_id": "payments-api",
                }
            ],
            "targets": [
                {
                    "platform": "linkedin",
                    "what_to_shorten": ["background context"],
                    "what_requires_expansion": ["practical conclusion"],
                    "target_platform_differences": ["short-form feed structure"],
                    "target_content_skill": {"skill_id": "linkedin-writing"},
                    "previous_related_content": [],
                }
            ],
        },
    )

    novelty = await call(
        api,
        "check_content_novelty",
        {
            "profile_id": PROFILE,
            "title": "An operational Outbox angle",
            "description": "Monitor delivery lag instead of implementation details.",
            "tags": ["outbox"],
            "project_id": "payments-api",
        },
    )
    plan = await call(
        api,
        "prepare_repurpose_plan",
        {
            "profile_id": PROFILE,
            "content_id": "outbox-post",
            "targets": ["linkedin"],
        },
    )
    refused = await call(
        api,
        "check_content_novelty",
        {"profile_id": OTHER_PROFILE, "title": "No", "description": "No."},
        authorization=build_authorization(["content:read"], profiles=[PROFILE]),
    )

    assert api.paths == ["/api/v2/content/novelty", "/api/v2/content/outbox-post/repurpose"]
    assert api.body(0)["project_id"] == "payments-api"
    assert "content_id" not in api.body(1)
    assert novelty.structuredContent["classification"] == "same-topic-new-angle"
    assert plan.structuredContent["plan"]["metadata"]["targets"][0]["platform"] == "linkedin"
    assert "advisory" in str(novelty.content[0].text)
    assert refused.isError is True
    assert refused.isError is True


@pytest.mark.asyncio
async def test_get_content_metrics_mcp_forwards_only_authorized_time_series_filters() -> None:
    api = RecordedApi()
    api.respond(
        "GET",
        "/api/v2/content/outbox-post/metrics",
        {
            "profile_id": PROFILE,
            "content_id": "outbox-post",
            "items": [
                {
                    "profile_id": PROFILE,
                    "metric_id": "linkedin-day-2",
                    "content_id": "outbox-post",
                    "platform": "linkedin",
                    "captured_at": "2026-09-22T00:00:00Z",
                    "metrics": {"impressions": 120, "likes": 7},
                    "source": "manual",
                    "provider_metadata": {},
                    "status": "active",
                    "revision": 1,
                    "created_at": "2026-09-22T00:00:00Z",
                    "updated_at": "2026-09-22T00:00:00Z",
                }
            ],
            "next_cursor": None,
        },
    )

    result = await call(
        api,
        "get_content_metrics",
        {
            "profile_id": PROFILE,
            "content_id": "outbox-post",
            "platform": "linkedin",
            "captured_after": "2026-09-20T00:00:00Z",
            "limit": 5,
        },
    )

    assert api.paths == ["/api/v2/content/outbox-post/metrics"]
    assert api.query(0) == {
        "profile_id": PROFILE,
        "platform": "linkedin",
        "captured_after": "2026-09-20T00:00:00Z",
        "limit": "5",
    }
    assert result.structuredContent["metrics"][0]["metadata"]["metric_id"] == "linkedin-day-2"
    assert "not instructions" in str(result.content[0].text)


@pytest.mark.asyncio
async def test_fact_review_mcp_calls_the_v2_api_and_keeps_citations_visible() -> None:
    api = RecordedApi()
    api.respond(
        "POST",
        "/api/v2/content/outbox-post/fact-review",
        {
            "profile_id": PROFILE,
            "content_id": "outbox-post",
            "research_project_id": "outbox-research",
            "factual_review_status": "needs_review",
            "supported": 1,
            "unsupported": 1,
            "contested": 0,
            "outdated": 0,
            "findings": [
                {
                    "statement": "The outbox persists payment events before delivery.",
                    "classification": "supported",
                    "claim_id": "outbox-ordering",
                    "claim_state": "supported",
                    "citation_refs": [],
                    "missing_evidence": None,
                },
                {
                    "statement": "The outbox guarantees exactly-once delivery.",
                    "classification": "unsupported",
                    "claim_id": "outbox-exactly-once",
                    "claim_state": "unsupported",
                    "citation_refs": [],
                    "missing_evidence": None,
                },
            ],
            "citation_refs": [
                {
                    "citation_id": "outbox-standard",
                    "claim_id": "outbox-ordering",
                    "source_id": "outbox-spec",
                    "snapshot_id": "outbox-spec-v1",
                    "chunk_id": "outbox-chunk",
                    "source_class": "standard",
                    "locator": {
                        "url": "https://example.test/outbox#delivery",
                        "page": None,
                        "anchor": None,
                        "path": None,
                        "line_start": None,
                        "line_end": None,
                        "commit": None,
                    },
                    "evidence_excerpt": "Persist an event before attempting delivery.",
                    "relation": "supports",
                }
            ],
            "missing_evidence": [],
            "disclaimer": "This is an evidence mapping, not a truth guarantee.",
        },
    )

    result = await call(
        api,
        "review_content_facts",
        {
            "profile_id": PROFILE,
            "content_id": "outbox-post",
            "research_project_id": "outbox-research",
            "claim_ids": ["outbox-ordering", "outbox-exactly-once"],
        },
        authorization=build_authorization(["content:read"], profiles=[PROFILE]),
    )

    assert api.paths == ["/api/v2/content/outbox-post/fact-review"]
    assert api.body(0) == {
        "profile_id": PROFILE,
        "research_project_id": "outbox-research",
        "claim_ids": ["outbox-ordering", "outbox-exactly-once"],
        "claim_statements": [],
    }
    assert result.structuredContent["findings"][1]["classification"] == "unsupported"
    assert result.structuredContent["citation_refs"][0]["locator"]["url"].endswith("#delivery")
    assert "does not guarantee factual truth" in str(result.content[0].text)
