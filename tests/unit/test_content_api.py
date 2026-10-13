"""V2 Phase 3 REST contracts, scopes, and project/profile isolation."""

from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import (
    get_content_discovery_service,
    get_content_export_service,
    get_content_novelty_service,
    get_content_repurpose_service,
    get_content_research_service,
    get_content_retrieval_service,
    get_content_service,
    get_research_project_service,
)
from app.api.routes.content import router
from app.content.errors import InvalidContentLifecycleTransitionError
from app.content.platforms.base import ContentPlatformValidationError, PlatformExport
from app.models.common import Page
from app.models.content import (
    ContentBriefDocument,
    ContentBriefStatus,
    ContentFactualReviewStatus,
    ContentIdeaDocument,
    ContentItemDocument,
    ContentPlatform,
    ContentSourceReference,
    ContentStatus,
    ContentVersionDocument,
)
from app.models.research import ResearchProjectDocument, ResearchProjectType
from app.schemas.content import ContentSearchRequest
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.content_discovery_service import (
    ContentBriefPreparation,
    ContentIdeaCandidate,
    ContentIdeaDiscovery,
    DiscoveryEvidence,
    DiscoveryFreshness,
    SkillReference,
)
from app.services.content_novelty_service import ContentNoveltyResult, SimilarContent
from app.services.content_repurpose_service import (
    RelatedContent,
    RepurposePlan,
    RepurposeTargetPlan,
)
from app.services.content_research_service import (
    ContentFactFinding,
    ContentFactReview,
    FactReviewClassification,
)
from app.services.content_retrieval_service import ContentSearchItem, ContentSearchResult
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROFILE = "personal-brand"
OTHER_PROFILE = "other-brand"
PROJECT = "payments-api"


def idea() -> ContentIdeaDocument:
    return ContentIdeaDocument(
        profile_id=PROFILE,
        idea_id="idempotency",
        title="Why idempotency matters",
        description="A practical explanation of safe payment retries.",
        platforms=["linkedin"],
        origin={"type": "project_context", "project_id": PROJECT, "source_ids": ["retry-rule"]},
        priority=0.9,
    )


def brief() -> ContentBriefDocument:
    return ContentBriefDocument(
        profile_id=PROFILE,
        brief_id="idempotency-linkedin",
        idea_id="idempotency",
        platform="linkedin",
        objective="Explain safe idempotent retries.",
        primary_message="Idempotency makes retries safe.",
        source_refs=[
            {"source_type": "project_context", "source_id": "retry-rule", "project_id": PROJECT}
        ],
    )


def item() -> ContentItemDocument:
    return ContentItemDocument(
        profile_id=PROFILE,
        content_id="idempotency-post",
        brief_id="idempotency-linkedin",
        platform="linkedin",
        content_type="post",
        title="Why idempotency matters",
        body="Draft body about safe payment retries.",
        project_ids=[PROJECT],
        source_refs=[
            {"source_type": "project_context", "source_id": "retry-rule", "project_id": PROJECT}
        ],
    )


class ContentDouble:
    """A route-level service double with every requested endpoint operation."""

    def __init__(self) -> None:
        self.idea = idea()
        self.brief = brief()
        self.item = item()
        self.list_ideas = AsyncMock(return_value=Page(items=(self.idea,), next_cursor="idea-next"))
        self.get_idea = AsyncMock(return_value=self.idea)
        self.create_idea = AsyncMock(return_value=self.idea)
        self.update_idea = AsyncMock(return_value=self.idea.model_copy(update={"revision": 2}))
        self.create_brief = AsyncMock(return_value=self.brief)
        self.get_brief = AsyncMock(return_value=self.brief)
        self.create_content = AsyncMock(return_value=self.item)
        self.get_content = AsyncMock(return_value=self.item)
        self.update_content = AsyncMock(return_value=self.item.model_copy(update={"revision": 2}))
        self.create_version = AsyncMock(
            return_value=self.item.model_copy(update={"current_version": 2, "revision": 2})
        )
        self.rollback_content = AsyncMock(
            return_value=self.item.model_copy(update={"current_version": 3, "revision": 3})
        )
        self.list_versions = AsyncMock(
            return_value=Page(
                items=(
                    ContentVersionDocument(
                        profile_id=PROFILE,
                        content_id=self.item.content_id,
                        version=1,
                        title=self.item.title,
                        body=self.item.body,
                        status="drafting",
                    ),
                ),
                next_cursor="history-next",
            )
        )


class ResearchProjectDouble:
    """The authorized V3 project lookup used only by Phase 13 route tests."""

    async def get_project(self, research_project_id: str) -> ResearchProjectDocument:
        assert research_project_id == "outbox-research"
        return ResearchProjectDocument(
            research_project_id=research_project_id,
            name="Outbox research",
            question="What can the draft claim?",
            type=ResearchProjectType.CONTENT_RESEARCH,
            related_project_ids=[PROJECT],
            authorization={"project_ids": [PROJECT]},
        )


class ContentFactReviewDouble:
    """A read-only review double whose result includes a visible unsupported claim."""

    def __init__(self) -> None:
        self.review_content_facts = AsyncMock(
            return_value=ContentFactReview(
                content_id="idempotency-post",
                research_project_id="outbox-research",
                factual_review_status=ContentFactualReviewStatus.NEEDS_REVIEW,
                supported=1,
                unsupported=1,
                contested=0,
                outdated=0,
                findings=(
                    ContentFactFinding(
                        statement="Retries reuse an idempotency key.",
                        classification=FactReviewClassification.SUPPORTED,
                        claim_id="retry-key",
                        claim_state="supported",
                        citation_refs=(),
                        missing_evidence=None,
                    ),
                    ContentFactFinding(
                        statement="Retries are always free.",
                        classification=FactReviewClassification.UNSUPPORTED,
                        claim_id="free-retries",
                        claim_state="unsupported",
                        citation_refs=(),
                        missing_evidence=None,
                    ),
                ),
                citation_refs=(),
                missing_evidence=(),
            )
        )


class ContentRetrievalDouble:
    """V2 retrieval response double containing both allowed and private results."""

    def __init__(self) -> None:
        self.search = AsyncMock(
            return_value=ContentSearchResult(
                profile_id=PROFILE,
                retrieval_profile="content_history",
                method="hybrid",
                token_budget=500,
                tokens_used=30,
                diagnostics={},
                items=(
                    ContentSearchItem(
                        kind="content",
                        identifier="payment-post",
                        title="Payment idempotency",
                        content="A public payment engineering post.",
                        status="published",
                        platforms=("linkedin",),
                        tags=("payments",),
                        pillar_id="payments",
                        project_ids=(PROJECT,),
                        source_refs=(),
                        score=0.9,
                        methods=("vector", "lexical"),
                        tokens=10,
                        updated_at=item().updated_at,
                    ),
                    ContentSearchItem(
                        kind="content",
                        identifier="private-post",
                        title="Private architecture note",
                        content="This response must not leak.",
                        status="drafting",
                        platforms=("linkedin",),
                        tags=("private",),
                        pillar_id="payments",
                        project_ids=("other-project",),
                        source_refs=(),
                        score=0.8,
                        methods=("lexical",),
                        tokens=20,
                        updated_at=item().updated_at,
                    ),
                ),
            )
        )


class ContentDiscoveryDouble:
    """A route-level Phase 5 assembler double; it does not persist anything."""

    def __init__(self) -> None:
        source = ContentSourceReference(
            source_type="project_context",
            source_id="retry-rule",
            project_id=PROJECT,
        )
        freshness = DiscoveryFreshness(
            updated_at=idea().updated_at,
            age_days=0,
            inside_requested_range=True,
        )
        evidence = DiscoveryEvidence(
            source=source,
            source_type="project_context",
            title="Idempotent payment retries",
            excerpt="Retries reuse the original payment request key.",
            tags=("payments", "idempotency"),
            freshness=freshness,
        )
        self.discover_content_ideas = AsyncMock(
            return_value=ContentIdeaDiscovery(
                profile_id=PROFILE,
                project_id=PROJECT,
                pillar_id="payments",
                platform=ContentPlatform.LINKEDIN,
                candidates=(
                    ContentIdeaCandidate(
                        topic=evidence.title,
                        source_refs=(evidence.source,),
                        evidence=(evidence,),
                        related_project_id=PROJECT,
                        related_pillar_id="payments",
                        suggested_platforms=(ContentPlatform.LINKEDIN,),
                        why_useful=("recent project context",),
                        previous_similar_content=(),
                        freshness=freshness,
                    ),
                ),
                existing_unpublished_ideas=(),
            )
        )
        self.prepare_content_brief = AsyncMock(
            return_value=ContentBriefPreparation(
                profile_id=PROFILE,
                idea_id="idempotency",
                platform=ContentPlatform.LINKEDIN,
                objective="Prepare a linkedin piece about Idempotent payment retries",
                audience=("backend engineers",),
                primary_message="Retries reuse the original payment request key.",
                supporting_points=("Idempotent payment retries",),
                source_refs=(evidence.source,),
                technical_skills=(
                    SkillReference("payments-engineering", "Payments Engineering", "1.0.0"),
                ),
                content_skill=SkillReference("linkedin-writing", "LinkedIn Writing", "1.0.0"),
                similar_previous_content=(),
                content_constraints=("do not fabricate project experiences",),
            )
        )


class ContentNoveltyDouble:
    """Phase 6 overlap double; classification remains explicitly advisory."""

    def __init__(self) -> None:
        self.check_content_novelty = AsyncMock(
            return_value=ContentNoveltyResult(
                profile_id=PROFILE,
                similar_content=(
                    SimilarContent(
                        kind="content",
                        identifier="idempotency-post",
                        title="Why idempotency matters",
                        platforms=("linkedin",),
                        status="published",
                        pillar_id="payments",
                        project_ids=(PROJECT,),
                        source_refs=(
                            {
                                "source_type": "project_context",
                                "source_id": "retry-rule",
                                "project_id": PROJECT,
                            },
                        ),
                        retrieval_score=0.9,
                        topic_overlap=0.8,
                        angle_overlap=0.2,
                        shared_topic_terms=("idempotency",),
                        shared_angle_terms=(),
                        classification="same-topic-new-angle",
                    ),
                ),
                topic_overlap=0.8,
                angle_overlap=0.2,
                classification="same-topic-new-angle",
            )
        )


class ContentRepurposeDouble:
    """Phase 6 non-generative plan double for route and authorization tests."""

    def __init__(self) -> None:
        source = ContentSourceReference(
            source_type="project_context", source_id="retry-rule", project_id=PROJECT
        )
        related = RelatedContent(
            kind="content",
            identifier="previous-post",
            title="A previous idempotency post",
            platforms=("medium",),
            status="published",
            source_refs=(source.model_dump(mode="python"),),
        )
        self.prepare_repurpose_plan = AsyncMock(
            return_value=RepurposePlan(
                profile_id=PROFILE,
                source_kind="content",
                source_id="idempotency-post",
                source_platform=ContentPlatform.LINKEDIN,
                core_thesis="Why idempotency matters",
                reusable_supporting_points=("Retries are safe with a stable request key.",),
                examples=(),
                source_refs=(source,),
                targets=(
                    RepurposeTargetPlan(
                        platform=ContentPlatform.MEDIUM,
                        what_to_shorten=("repeated framing",),
                        what_requires_expansion=("technical depth",),
                        target_platform_differences=("long-form article structure",),
                        target_content_skill=None,
                        previous_related_content=(related,),
                    ),
                ),
            )
        )


class ContentExportDouble:
    """Phase 7 formatter double; it has no persistence or provider behavior."""

    def __init__(self) -> None:
        self.export_content = Mock(
            return_value=PlatformExport(
                profile_id=PROFILE,
                content_id="idempotency-post",
                platform=ContentPlatform.LINKEDIN,
                mode="markdown",
                format="plain_text",
                text="A manually formatted export.\n",
                suggested_file_name="idempotency.txt",
                suggested_path=None,
                metadata=MappingProxyType({"representation": "plain_text"}),
            )
        )


def application(service: ContentDouble) -> Any:
    app = open_application(router)
    app.dependency_overrides[get_content_service] = lambda: service
    return app


def secured_application(
    service: ContentDouble,
    *,
    scopes: tuple[str, ...] = ("content:read", "content:write"),
    profiles: tuple[str, ...] = (PROFILE,),
    projects: tuple[str, ...] = (PROJECT,),
) -> tuple[Any, str, FakeAuditRepository]:
    record, token = issued_key(scopes=scopes, profiles=profiles, projects=projects)  # type: ignore[arg-type]
    audit = FakeAuditRepository()
    app = authenticated_application(
        router,
        auth=AuthService(FakeApiKeyRepository(record)),
        audit=AuditService(audit),
    )
    app.dependency_overrides[get_content_service] = lambda: service
    return app, token, audit


def fact_review_application(
    service: ContentDouble,
    review: ContentFactReviewDouble,
    *,
    projects: tuple[str, ...] = (PROJECT,),
) -> tuple[Any, str]:
    app, token, _ = secured_application(service, scopes=("content:read",), projects=projects)
    app.dependency_overrides[get_content_research_service] = lambda: review
    app.dependency_overrides[get_research_project_service] = ResearchProjectDouble
    return app, token


def search_application(
    service: ContentDouble,
    retrieval: ContentRetrievalDouble,
    *,
    secured: bool = False,
) -> tuple[Any, str | None]:
    if secured:
        app, token, _ = secured_application(service)
    else:
        app = application(service)
        token = None
    app.dependency_overrides[get_content_retrieval_service] = lambda: retrieval
    return app, token


def discovery_application(
    service: ContentDouble,
    discovery: ContentDiscoveryDouble,
    *,
    projects: tuple[str, ...] = (PROJECT,),
) -> tuple[Any, str]:
    app, token, _ = secured_application(service, projects=projects)
    app.dependency_overrides[get_content_discovery_service] = lambda: discovery
    return app, token


def phase6_application(
    service: ContentDouble,
    novelty: ContentNoveltyDouble,
    repurpose: ContentRepurposeDouble,
    *,
    projects: tuple[str, ...] = (PROJECT,),
) -> tuple[Any, str]:
    app, token, _ = secured_application(service, projects=projects)
    app.dependency_overrides[get_content_novelty_service] = lambda: novelty
    app.dependency_overrides[get_content_repurpose_service] = lambda: repurpose
    return app, token


def phase7_application(
    service: ContentDouble,
    exporting: ContentExportDouble,
    *,
    projects: tuple[str, ...] = (PROJECT,),
) -> tuple[Any, str]:
    app, token, _ = secured_application(service, projects=projects)
    app.dependency_overrides[get_content_export_service] = lambda: exporting
    return app, token


async def call(
    app: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=body, headers=headers)


def idea_payload() -> dict[str, Any]:
    return {
        "profile_id": PROFILE,
        "idea_id": "idempotency",
        "title": "Why idempotency matters",
        "description": "A practical explanation of safe payment retries.",
        "platforms": ["linkedin"],
        "origin": {"type": "project_context", "project_id": PROJECT, "source_ids": ["retry-rule"]},
        "priority": 0.9,
    }


def content_payload() -> dict[str, Any]:
    return {
        "profile_id": PROFILE,
        "content_id": "idempotency-post",
        "brief_id": "idempotency-linkedin",
        "platform": "linkedin",
        "content_type": "post",
        "title": "Why idempotency matters",
        "body": "Draft body about safe payment retries.",
        "project_ids": [PROJECT],
        "source_refs": [
            {"source_type": "project_context", "source_id": "retry-rule", "project_id": PROJECT}
        ],
        "created_by": "editor",
    }


@pytest.mark.asyncio
async def test_content_routes_create_read_update_and_version_a_draft_without_generation() -> None:
    service = ContentDouble()
    app = application(service)

    created_idea = await call(app, "POST", "/api/v2/content-ideas", idea_payload())
    listed = await call(app, "GET", f"/api/v2/content-ideas?profile_id={PROFILE}&limit=3")
    updated_idea = await call(
        app,
        "PATCH",
        "/api/v2/content-ideas/idempotency",
        {"profile_id": PROFILE, "expected_revision": 1, "status": "selected"},
    )
    created_brief = await call(
        app,
        "POST",
        "/api/v2/content-briefs",
        {
            "profile_id": PROFILE,
            "brief_id": "idempotency-linkedin",
            "idea_id": "idempotency",
            "platform": "linkedin",
            "objective": "Explain safe idempotent retries.",
            "primary_message": "Idempotency makes retries safe.",
        },
    )
    loaded_brief = await call(
        app,
        "GET",
        "/api/v2/content-briefs/idempotency-linkedin?profile_id=personal-brand",
    )
    created_content = await call(app, "POST", "/api/v2/content", content_payload())
    loaded_content = await call(
        app,
        "GET",
        "/api/v2/content/idempotency-post?profile_id=personal-brand",
    )
    updated_content = await call(
        app,
        "PATCH",
        "/api/v2/content/idempotency-post",
        {"profile_id": PROFILE, "expected_revision": 1, "status": "review"},
    )
    versioned = await call(
        app,
        "POST",
        "/api/v2/content/idempotency-post/versions",
        {
            "profile_id": PROFILE,
            "expected_revision": 1,
            "body": "Second reviewed draft about safe retries.",
            "change_reason": "clarify the request key",
            "created_by": "editor",
        },
    )
    rollback = await call(
        app,
        "POST",
        "/api/v2/content/idempotency-post/versions",
        {
            "profile_id": PROFILE,
            "expected_revision": 2,
            "rollback_to_version": 1,
            "change_reason": "restore the approved opening",
            "created_by": "editor",
        },
    )

    assert created_idea.status_code == 201
    assert listed.json()["next_cursor"] == "idea-next"
    assert updated_idea.status_code == 200
    assert created_brief.status_code == 201
    assert loaded_brief.status_code == 200
    assert created_content.status_code == 201
    assert loaded_content.status_code == 200
    assert updated_content.status_code == 200
    assert versioned.json()["current_version"] == 2
    assert rollback.json()["current_version"] == 3
    assert service.create_content.await_args.kwargs["body"].startswith("Draft")
    assert service.create_version.await_args.kwargs["created_by"] == "editor"
    assert service.rollback_content.await_args.kwargs["rollback_to_version"] == 1


@pytest.mark.asyncio
async def test_content_route_validation_refuses_body_overwrite_and_ambiguous_rollback() -> None:
    service = ContentDouble()
    app = application(service)

    direct_body = await call(
        app,
        "PATCH",
        "/api/v2/content/idempotency-post",
        {"profile_id": PROFILE, "expected_revision": 1, "body": "Overwrite history"},
    )
    ambiguous_version = await call(
        app,
        "POST",
        "/api/v2/content/idempotency-post/versions",
        {
            "profile_id": PROFILE,
            "expected_revision": 1,
            "body": "A draft",
            "rollback_to_version": 1,
            "change_reason": "ambiguous",
        },
    )
    invalid_platform = await call(
        app,
        "POST",
        "/api/v2/content-ideas",
        {**idea_payload(), "platforms": ["x"]},
    )

    assert direct_body.status_code == 422
    assert ambiguous_version.status_code == 422
    assert invalid_platform.status_code == 422
    service.update_content.assert_not_awaited()
    service.create_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_routes_enforce_content_scope_profile_and_project_before_write() -> None:
    service = ContentDouble()
    app, token, audit = secured_application(service, projects=("other-project",))

    denied_project = await call(
        app,
        "POST",
        "/api/v2/content",
        content_payload(),
        headers=bearer(token),
    )
    denied_profile = await call(
        app,
        "GET",
        f"/api/v2/content-ideas?profile_id={OTHER_PROFILE}",
        headers=bearer(token),
    )
    assert denied_project.status_code == 403
    assert denied_profile.status_code == 403
    assert PROJECT not in denied_project.text
    service.create_content.assert_not_awaited()
    service.list_ideas.assert_not_awaited()
    assert ("authorize", "denied") in audit.actions()

    scope_app, read_token, _ = secured_application(service, scopes=("content:read",))
    denied_scope = await call(
        scope_app,
        "POST",
        "/api/v2/content-ideas",
        idea_payload(),
        headers=bearer(read_token),
    )
    assert denied_scope.status_code == 403
    service.create_idea.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_route_reports_invalid_lifecycle_without_a_traceback() -> None:
    service = ContentDouble()
    service.update_content.side_effect = InvalidContentLifecycleTransitionError(
        "content cannot transition from published to drafting"
    )
    app = application(service)

    response = await call(
        app,
        "PATCH",
        "/api/v2/content/idempotency-post",
        {"profile_id": PROFILE, "expected_revision": 1, "status": "drafting"},
    )

    assert response.status_code == 422
    assert "Traceback" not in response.text


@pytest.mark.asyncio
async def test_content_search_enforces_profile_scope_and_filters_private_project_results() -> None:
    service = ContentDouble()
    retrieval = ContentRetrievalDouble()
    app, token = search_application(service, retrieval, secured=True)
    assert token is not None

    response = await call(
        app,
        "POST",
        "/api/v2/content/search",
        {
            "profile_id": PROFILE,
            "query": "payment idempotency",
            "kinds": ["content"],
            "platform": "linkedin",
            "project_id": PROJECT,
            "status": "published",
            "pillar_id": "payments",
            "tags": ["payments"],
            "token_budget": 200,
        },
        headers=bearer(token),
    )
    refused = await call(
        app,
        "POST",
        "/api/v2/content/search",
        {"profile_id": OTHER_PROFILE, "query": "payment idempotency"},
        headers=bearer(token),
    )

    assert response.status_code == 200
    assert [entry["identifier"] for entry in response.json()["items"]] == ["payment-post"]
    assert response.json()["tokens_used"] == 10
    assert refused.status_code == 403
    assert retrieval.search.await_args.kwargs["filters"].project_id == PROJECT
    assert retrieval.search.await_args.kwargs["filters"].platform == "linkedin"
    assert retrieval.search.await_args.kwargs["kinds"] == ("content",)


def test_content_search_accepts_a_brief_lifecycle_status() -> None:
    """A caller can filter the brief-specific lifecycle without using raw strings."""
    request = ContentSearchRequest(profile_id=PROFILE, query="outbox", status="ready")

    assert request.status is ContentBriefStatus.READY


@pytest.mark.asyncio
async def test_content_history_is_read_through_current_project_provenance() -> None:
    service = ContentDouble()
    app = application(service)

    response = await call(
        app,
        "GET",
        f"/api/v2/content/{service.item.content_id}/versions?profile_id={PROFILE}&limit=5",
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["version"] == 1
    assert response.json()["next_cursor"] == "history-next"
    service.list_versions.assert_awaited_once_with(
        PROFILE, service.item.content_id, limit=5, cursor=None
    )


@pytest.mark.asyncio
async def test_content_discovery_and_brief_routes_return_evidence_without_persisting() -> None:
    service = ContentDouble()
    discovery = ContentDiscoveryDouble()
    app, token = discovery_application(service, discovery)

    discovered = await call(
        app,
        "POST",
        "/api/v2/content-ideas/discover",
        {
            "profile_id": PROFILE,
            "project_id": PROJECT,
            "pillar_id": "payments",
            "platform": "linkedin",
            "time_range": {"start_at": "2026-09-01T00:00:00Z"},
        },
        headers=bearer(token),
    )
    prepared = await call(
        app,
        "POST",
        "/api/v2/content-briefs/prepare",
        {"profile_id": PROFILE, "idea_id": "idempotency", "platform": "linkedin"},
        headers=bearer(token),
    )

    assert discovered.status_code == 200
    assert discovered.json()["candidates"][0]["source_refs"][0]["source_id"] == "retry-rule"
    assert prepared.status_code == 200
    assert prepared.json()["content_skill"]["skill_id"] == "linkedin-writing"
    assert prepared.json()["content_constraints"] == ["do not fabricate project experiences"]
    assert discovery.discover_content_ideas.await_args.kwargs["time_range"].start_at is not None
    assert discovery.prepare_content_brief.await_args.kwargs["platform"] == "linkedin"
    service.create_idea.assert_not_awaited()
    service.create_brief.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_discovery_routes_refuse_other_profiles_and_private_idea_provenance() -> None:
    service = ContentDouble()
    discovery = ContentDiscoveryDouble()
    app, token = discovery_application(service, discovery, projects=("other-project",))

    denied_profile = await call(
        app,
        "POST",
        "/api/v2/content-ideas/discover",
        {"profile_id": OTHER_PROFILE},
        headers=bearer(token),
    )
    denied_project = await call(
        app,
        "POST",
        "/api/v2/content-briefs/prepare",
        {"profile_id": PROFILE, "idea_id": "idempotency", "platform": "linkedin"},
        headers=bearer(token),
    )

    assert denied_profile.status_code == 403
    assert denied_project.status_code == 403
    assert PROJECT not in denied_project.text
    discovery.discover_content_ideas.assert_not_awaited()
    discovery.prepare_content_brief.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_novelty_route_returns_advisory_overlap_with_authorization() -> None:
    service = ContentDouble()
    novelty = ContentNoveltyDouble()
    repurpose = ContentRepurposeDouble()
    app, token = phase6_application(service, novelty, repurpose)

    response = await call(
        app,
        "POST",
        "/api/v2/content/novelty",
        {
            "profile_id": PROFILE,
            "title": "Idempotency from a new operational angle",
            "description": "Show retry monitoring rather than request-key implementation.",
            "tags": ["idempotency"],
            "project_id": PROJECT,
        },
        headers=bearer(token),
    )
    denied = await call(
        app,
        "POST",
        "/api/v2/content/novelty",
        {
            "profile_id": PROFILE,
            "title": "Private candidate",
            "description": "Must not reach private project history.",
            "project_id": "other-project",
        },
        headers=bearer(token),
    )

    assert response.status_code == 200
    assert response.json()["advisory"] is True
    assert response.json()["similar_content"][0]["shared_topic_terms"] == ["idempotency"]
    assert novelty.check_content_novelty.await_args.kwargs["allowed_project_ids"] == frozenset(
        {PROJECT}
    )
    assert denied.status_code == 403
    assert "other-project" not in denied.text


@pytest.mark.asyncio
async def test_repurpose_route_loads_and_authorizes_source_before_building_a_plan() -> None:
    service = ContentDouble()
    novelty = ContentNoveltyDouble()
    repurpose = ContentRepurposeDouble()
    app, token = phase6_application(service, novelty, repurpose)

    response = await call(
        app,
        "POST",
        "/api/v2/content/idempotency-post/repurpose",
        {"profile_id": PROFILE, "targets": ["medium"]},
        headers=bearer(token),
    )
    denied_app, denied_token = phase6_application(
        service, novelty, repurpose, projects=("other-project",)
    )
    denied = await call(
        denied_app,
        "POST",
        "/api/v2/content/idempotency-post/repurpose",
        {"profile_id": PROFILE, "targets": ["medium"]},
        headers=bearer(denied_token),
    )

    assert response.status_code == 200
    assert response.json()["targets"][0]["platform"] == "medium"
    target = response.json()["targets"][0]
    assert target["previous_related_content"][0]["identifier"] == "previous-post"
    assert (
        repurpose.prepare_repurpose_plan.await_args.kwargs["source"].content_id
        == "idempotency-post"
    )
    assert denied.status_code == 403
    assert PROJECT not in denied.text


@pytest.mark.asyncio
async def test_content_export_formats_only_an_authorized_item_without_publishing() -> None:
    service = ContentDouble()
    service.item = service.item.model_copy(update={"status": ContentStatus.APPROVED})
    service.get_content.return_value = service.item
    exporting = ContentExportDouble()
    app, token = phase7_application(service, exporting)

    response = await call(
        app,
        "POST",
        "/api/v2/content/idempotency-post/export",
        {"profile_id": PROFILE, "mode": "markdown"},
        headers=bearer(token),
    )

    assert response.status_code == 200
    assert response.json() == {
        "profile_id": PROFILE,
        "content_id": "idempotency-post",
        "platform": "linkedin",
        "mode": "markdown",
        "format": "plain_text",
        "text": "A manually formatted export.\n",
        "suggested_file_name": "idempotency.txt",
        "suggested_path": None,
        "metadata": {"representation": "plain_text"},
    }
    exporting.export_content.assert_called_once_with(service.item, mode="markdown")
    assert service.item.status.value == "approved"
    assert service.item.external_url is None


@pytest.mark.asyncio
async def test_content_export_refuses_private_project_content_before_formatting() -> None:
    service = ContentDouble()
    exporting = ContentExportDouble()
    app, token = phase7_application(service, exporting, projects=("other-project",))

    response = await call(
        app,
        "POST",
        "/api/v2/content/idempotency-post/export",
        {"profile_id": PROFILE},
        headers=bearer(token),
    )

    assert response.status_code == 403
    assert PROJECT not in response.text
    exporting.export_content.assert_not_called()


@pytest.mark.asyncio
async def test_content_export_returns_validation_error_without_changing_content() -> None:
    service = ContentDouble()
    service.item = service.item.model_copy(update={"status": ContentStatus.APPROVED})
    service.get_content.return_value = service.item
    exporting = ContentExportDouble()
    exporting.export_content.side_effect = ContentPlatformValidationError("unclosed code fence")
    app, token = phase7_application(service, exporting)

    response = await call(
        app,
        "POST",
        "/api/v2/content/idempotency-post/export",
        {"profile_id": PROFILE},
        headers=bearer(token),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "unclosed code fence"
    assert service.item.status.value == "approved"
    assert service.item.current_version == 1


@pytest.mark.asyncio
async def test_content_export_requires_explicit_approval_before_formatting() -> None:
    service = ContentDouble()
    exporting = ContentExportDouble()
    app, token = phase7_application(service, exporting)

    response = await call(
        app,
        "POST",
        "/api/v2/content/idempotency-post/export",
        {"profile_id": PROFILE},
        headers=bearer(token),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "content must be approved before it can be exported"
    exporting.export_content.assert_not_called()


@pytest.mark.asyncio
async def test_content_fact_review_is_authorized_advisory_and_never_rewrites_or_publishes() -> None:
    service = ContentDouble()
    service.brief = service.brief.model_copy(update={"research_project_id": "outbox-research"})
    service.get_brief.return_value = service.brief
    review = ContentFactReviewDouble()
    app, token = fact_review_application(service, review)
    original = service.item.model_dump(mode="json")

    response = await call(
        app,
        "POST",
        "/api/v2/content/idempotency-post/fact-review",
        {"profile_id": PROFILE, "claim_ids": ["retry-key", "free-retries"]},
        headers=bearer(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["factual_review_status"] == "needs_review"
    assert body["supported"] == 1
    assert body["unsupported"] == 1
    assert body["findings"][1]["classification"] == "unsupported"
    assert "does not guarantee factual truth" in body["disclaimer"]
    assert review.review_content_facts.await_args.kwargs["research_project_id"] == "outbox-research"
    assert service.item.model_dump(mode="json") == original
    service.update_content.assert_not_awaited()
    service.create_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_fact_review_refuses_private_project_before_reading_research_evidence() -> (
    None
):
    service = ContentDouble()
    service.brief = service.brief.model_copy(update={"research_project_id": "outbox-research"})
    service.get_brief.return_value = service.brief
    review = ContentFactReviewDouble()
    app, token = fact_review_application(service, review, projects=("other-project",))

    response = await call(
        app,
        "POST",
        "/api/v2/content/idempotency-post/fact-review",
        {"profile_id": PROFILE},
        headers=bearer(token),
    )

    assert response.status_code == 403
    assert PROJECT not in response.text
    review.review_content_facts.assert_not_awaited()
