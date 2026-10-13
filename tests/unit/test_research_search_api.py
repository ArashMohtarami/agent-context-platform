"""V3 Phase 5 REST search authorization and transport tests."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_research_project_service, get_research_search_service
from app.api.routes.research_search import router
from app.models.research import (
    ResearchAuthorization,
    ResearchProjectDocument,
    ResearchProjectType,
    ResearchSourceType,
    SourceClass,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.research_search_service import ResearchSearchItem, ResearchSearchResult
from tests.api_doubles import authenticated_application, open_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROJECT = "payments-api"
PRIVATE = "private-ledger"
RESEARCH = "retry-evidence"
WHEN = datetime(2026, 9, 30, tzinfo=UTC)


def project(links: list[str] | None = None) -> ResearchProjectDocument:
    related = [PROJECT] if links is None else links
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Retry evidence",
        question="What should retries do?",
        type=ResearchProjectType.TECHNICAL_RESEARCH,
        related_project_ids=related,
        authorization=ResearchAuthorization(project_ids=related),
    )


def result() -> ResearchSearchResult:
    item = ResearchSearchItem(
        kind="chunk",
        identifier="retry-chunk",
        source="research_primary",
        title="Retry guide",
        content="A retry needs an idempotency key.",
        status="active",
        source_id="retry-guide",
        snapshot_id="retry-snapshot",
        document_id="retry-document",
        source_type=ResearchSourceType.URL,
        source_class=SourceClass.PRIMARY,
        publisher="Example",
        domain="example.test",
        published_at=WHEN,
        retrieved_at=WHEN,
        locator={"url": "https://example.test/retries", "anchor": "keys"},
        related_project_ids=(PROJECT,),
        retrieval_score=0.91,
        methods=("lexical",),
        tokens=8,
        updated_at=WHEN,
    )
    return ResearchSearchResult(
        research_project_id=RESEARCH,
        retrieval_profile="research_general",
        method="lexical",
        items=(item,),
        token_budget=8_000,
        tokens_used=8,
        next_cursor="next",
        diagnostics={},
    )


class ProjectService:
    def __init__(self, record: ResearchProjectDocument) -> None:
        self.get_project = AsyncMock(return_value=record)


class SearchService:
    def __init__(self) -> None:
        self.search = AsyncMock(return_value=result())


def application(projects: ProjectService, search: SearchService) -> Any:
    app = open_application(router)
    app.dependency_overrides[get_research_project_service] = lambda: projects
    app.dependency_overrides[get_research_search_service] = lambda: search
    return app


def secured_application(projects: ProjectService, search: SearchService) -> tuple[Any, str]:
    key, token = issued_key(scopes=("research:read",), projects=(PROJECT,))
    app = authenticated_application(
        router,
        auth=AuthService(FakeApiKeyRepository(key)),
        audit=AuditService(FakeAuditRepository()),
    )
    app.dependency_overrides[get_research_project_service] = lambda: projects
    app.dependency_overrides[get_research_search_service] = lambda: search
    return app, token


async def call(app: Any, body: dict[str, Any], *, headers: dict[str, str] | None = None) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v3/research/search", json=body, headers=headers)


@pytest.mark.asyncio
async def test_search_route_forwards_bounded_filters_after_authorization() -> None:
    search = SearchService()
    response = await call(
        application(ProjectService(project()), search),
        {
            "research_project_id": RESEARCH,
            "query": "retry key",
            "method": "lexical",
            "source_classes": ["primary"],
            "domain": "EXAMPLE.TEST",
            "limit": 5,
        },
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["retrieval_score"] == 0.91
    assert search.search.await_args is not None
    kwargs = search.search.await_args.kwargs
    assert kwargs["filters"].source_classes == ("primary",)
    assert kwargs["filters"].domain == "example.test"
    assert kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_search_route_refuses_private_link_before_running_search() -> None:
    search = SearchService()
    app, token = secured_application(ProjectService(project([PRIVATE])), search)

    response = await call(
        app,
        {"research_project_id": RESEARCH, "query": "hidden retry data", "method": "lexical"},
        headers=bearer(token),
    )

    assert response.status_code == 403
    assert PRIVATE not in response.text
    search.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_route_rejects_unknown_related_project_without_searching() -> None:
    search = SearchService()
    response = await call(
        application(ProjectService(project()), search),
        {
            "research_project_id": RESEARCH,
            "query": "retry key",
            "method": "lexical",
            "related_project_id": PRIVATE,
        },
    )

    assert response.status_code == 422
    search.search.assert_not_awaited()
