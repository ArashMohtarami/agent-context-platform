"""Focused V2 Phase 10 generic content-metric behavior tests."""

from datetime import UTC, datetime
from math import inf
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api.dependencies import get_content_metrics_service, get_content_service
from app.api.routes.content import router
from app.content.errors import ContentMetricAlreadyExistsError
from app.models.common import Page
from app.models.content import (
    ContentItemDocument,
    ContentMetricDocument,
    ContentPlatform,
    ContentSourceReference,
    CreatorProfileDocument,
)
from app.repositories.errors import DuplicateDocumentError
from app.schemas.content import ContentMetricCreateRequest
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.content_metrics_service import ContentMetricsService
from tests.api_doubles import authenticated_application
from tests.security_doubles import FakeApiKeyRepository, FakeAuditRepository, bearer, issued_key

PROFILE = "personal-brand"
OTHER_PROFILE = "other-brand"
PROJECT = "payment-service"


def item(profile_id: str = PROFILE) -> ContentItemDocument:
    """Return one content item whose project access must guard its metrics."""
    return ContentItemDocument(
        profile_id=profile_id,
        content_id="idempotency-post",
        platform=ContentPlatform.LINKEDIN,
        content_type="post",
        topic_tags=[],
        title="Why idempotency matters",
        body="A practical explanation of safe payment retries.",
        project_ids=[PROJECT],
        source_refs=[
            ContentSourceReference(
                source_type="project_context",
                source_id="retry-policy",
                project_id=PROJECT,
            )
        ],
    )


class Profiles:
    """In-memory profile lookup; metric storage must never update these records."""

    def __init__(self) -> None:
        self.records = {
            PROFILE: CreatorProfileDocument(profile_id=PROFILE, name="Personal Brand"),
            OTHER_PROFILE: CreatorProfileDocument(profile_id=OTHER_PROFILE, name="Other Brand"),
        }

    async def get(self, profile_id: str) -> CreatorProfileDocument | None:
        return self.records.get(profile_id)


class Items:
    """One profile-bound current-content lookup."""

    def __init__(self, profile_id: str) -> None:
        self.records = {"idempotency-post": item(profile_id)}

    async def get(self, content_id: str) -> ContentItemDocument | None:
        return self.records.get(content_id)


class Metrics:
    """Append-only metric fixture with captured-at filtering and pagination bounds."""

    def __init__(self) -> None:
        self.records: dict[str, ContentMetricDocument] = {}

    async def create(self, document: ContentMetricDocument) -> ContentMetricDocument:
        if document.metric_id in self.records:
            raise DuplicateDocumentError("duplicate metric")
        self.records[document.metric_id] = document
        return document

    async def list_for_content(
        self,
        *,
        content_id: str,
        platform: ContentPlatform | None = None,
        captured_after: datetime | None = None,
        captured_before: datetime | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ContentMetricDocument]:
        del cursor
        values = [
            metric
            for metric in self.records.values()
            if metric.content_id == content_id
            and (platform is None or metric.platform is platform)
            and (captured_after is None or metric.captured_at >= captured_after)
            and (captured_before is None or metric.captured_at <= captured_before)
        ]
        values.sort(key=lambda metric: (metric.captured_at, metric.id), reverse=True)
        return Page(
            items=tuple(values[:limit]), next_cursor="metrics-next" if len(values) > limit else None
        )


class Fakes:
    """Keep all repositories isolated by creator profile."""

    def __init__(self) -> None:
        self.profiles = Profiles()
        self.items: dict[str, Items] = {}
        self.metrics: dict[str, Metrics] = {}

    def service(self) -> ContentMetricsService:
        return ContentMetricsService(
            self.profiles,
            lambda profile_id: self.items.setdefault(profile_id, Items(profile_id)),
            lambda profile_id: self.metrics.setdefault(profile_id, Metrics()),
        )


def capture_time(day: int) -> datetime:
    return datetime(2026, 9, day, tzinfo=UTC)


@pytest.mark.asyncio
async def test_manual_metric_storage_is_generic_time_ordered_and_never_mutates_a_profile() -> None:
    fakes = Fakes()
    service = fakes.service()
    before = fakes.profiles.records[PROFILE].model_dump(mode="python")

    first = await service.create_metric(
        profile_id=PROFILE,
        metric_id="linkedin-day-1",
        content_id="idempotency-post",
        platform=ContentPlatform.LINKEDIN,
        captured_at=capture_time(1),
        metrics={"views": 100, "likes": 5, "bookmarks": 2},
        source="manual",
        provider_metadata={"period": "24h"},
    )
    latest = await service.create_metric(
        profile_id=PROFILE,
        metric_id="medium-day-2",
        content_id="idempotency-post",
        platform=ContentPlatform.MEDIUM,
        captured_at=capture_time(2),
        metrics={"reads": 80, "read_ratio": 0.52},
        source="provider-export",
        provider_metadata={"report": "weekly"},
    )

    history = await service.list_metrics(profile_id=PROFILE, content_id="idempotency-post")

    assert history.page.items == (latest, first)
    assert latest.metrics["read_ratio"] == 0.52
    assert latest.source == "provider-export"
    assert fakes.profiles.records[PROFILE].model_dump(mode="python") == before


@pytest.mark.asyncio
async def test_metric_history_filters_platform_and_capture_range_with_cursor_pagination() -> None:
    fakes = Fakes()
    service = fakes.service()
    for metric_id, platform, day in (
        ("linkedin-day-1", ContentPlatform.LINKEDIN, 1),
        ("medium-day-2", ContentPlatform.MEDIUM, 2),
        ("linkedin-day-3", ContentPlatform.LINKEDIN, 3),
    ):
        await service.create_metric(
            profile_id=PROFILE,
            metric_id=metric_id,
            content_id="idempotency-post",
            platform=platform,
            captured_at=capture_time(day),
            metrics={"views": day},
        )

    filtered = await service.list_metrics(
        profile_id=PROFILE,
        content_id="idempotency-post",
        platform=ContentPlatform.LINKEDIN,
        captured_after=capture_time(2),
        limit=1,
    )

    assert [metric.metric_id for metric in filtered.page.items] == ["linkedin-day-3"]
    assert filtered.page.next_cursor is None


@pytest.mark.asyncio
async def test_metrics_are_profile_isolated_and_an_unconfigured_source_needs_no_provider() -> None:
    fakes = Fakes()
    service = fakes.service()
    stored = await service.create_metric(
        profile_id=PROFILE,
        metric_id="manual-export",
        content_id="idempotency-post",
        platform=ContentPlatform.LINKEDIN,
        captured_at=capture_time(1),
        metrics={"impressions": 10},
        source="provider-export",
    )

    other = await service.list_metrics(profile_id=OTHER_PROFILE, content_id="idempotency-post")

    assert stored.source == "provider-export"
    assert other.page.items == ()


@pytest.mark.asyncio
async def test_metric_duplicate_ids_are_conflicts_without_replacing_history() -> None:
    service = Fakes().service()
    first = await service.create_metric(
        profile_id=PROFILE,
        metric_id="same-capture",
        content_id="idempotency-post",
        platform=ContentPlatform.LINKEDIN,
        captured_at=capture_time(1),
        metrics={"views": 1},
    )

    with pytest.raises(ContentMetricAlreadyExistsError, match="already exists"):
        await service.create_metric(
            profile_id=PROFILE,
            metric_id="same-capture",
            content_id="idempotency-post",
            platform=ContentPlatform.LINKEDIN,
            captured_at=capture_time(1),
            metrics={"views": 1},
        )

    assert first.metrics == {"views": 1.0}


@pytest.mark.parametrize(
    "metrics",
    [
        {},
        {"views": -1},
        {"views": inf},
        {"views": True},
        {"views": "many"},
        {"invalid key": 1},
    ],
)
def test_invalid_metrics_are_rejected_at_model_and_api_schema_boundaries(
    metrics: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ContentMetricDocument(
            profile_id=PROFILE,
            metric_id="invalid",
            content_id="idempotency-post",
            platform=ContentPlatform.LINKEDIN,
            captured_at=capture_time(1),
            metrics=metrics,
        )
    with pytest.raises(ValidationError):
        ContentMetricCreateRequest(
            profile_id=PROFILE,
            metric_id="invalid",
            platform=ContentPlatform.LINKEDIN,
            captured_at=capture_time(1),
            metrics=metrics,
        )


class ContentLookup:
    """The existing content service surface used by the metric API routes."""

    def __init__(self) -> None:
        self.item = item()

    async def get_content(self, profile_id: str, content_id: str) -> ContentItemDocument:
        assert (profile_id, content_id) == (PROFILE, self.item.content_id)
        return self.item


def application(
    fakes: Fakes,
    audit: FakeAuditRepository,
    *,
    scopes: tuple[str, ...] = ("content:read", "content:write"),
    projects: tuple[str, ...] = (PROJECT,),
) -> tuple[Any, str]:
    """Build the real content router over in-memory metric services."""
    record, token = issued_key(scopes=scopes, profiles=(PROFILE,), projects=projects)  # type: ignore[arg-type]
    app = authenticated_application(
        router,
        auth=AuthService(FakeApiKeyRepository(record)),
        audit=AuditService(audit),
    )
    app.dependency_overrides[get_content_service] = lambda: ContentLookup()
    app.dependency_overrides[get_content_metrics_service] = fakes.service
    return app, token


@pytest.mark.asyncio
async def test_metrics_api_stores_reads_and_audits_manual_observations() -> None:
    fakes = Fakes()
    audit = FakeAuditRepository()
    app, token = application(fakes, audit)
    payload = {
        "profile_id": PROFILE,
        "metric_id": "api-day-1",
        "platform": "linkedin",
        "captured_at": "2026-09-01T00:00:00Z",
        "metrics": {"views": 120, "followers_gained": 3},
        "source": "manual",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v2/content/idempotency-post/metrics",
            json=payload,
            headers=bearer(token),
        )
        listed = await client.get(
            "/api/v2/content/idempotency-post/metrics",
            params={"profile_id": PROFILE, "platform": "linkedin"},
            headers=bearer(token),
        )
        invalid = await client.post(
            "/api/v2/content/idempotency-post/metrics",
            json={**payload, "metric_id": "bad-value", "metrics": {"views": -1}},
            headers=bearer(token),
        )

    assert created.status_code == 201
    assert created.json()["metrics"]["followers_gained"] == 3.0
    assert listed.status_code == 200
    assert [entry["metric_id"] for entry in listed.json()["items"]] == ["api-day-1"]
    assert invalid.status_code == 422
    assert audit.actions()[-1] == ("content.write", "allowed")


@pytest.mark.asyncio
async def test_metrics_api_enforces_content_scopes_and_project_grants() -> None:
    fakes = Fakes()
    app, read_only = application(fakes, FakeAuditRepository(), scopes=("content:read",))
    denied_app, denied_project = application(
        fakes,
        FakeAuditRepository(),
        projects=("other-project",),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        write_refused = await client.post(
            "/api/v2/content/idempotency-post/metrics",
            json={
                "profile_id": PROFILE,
                "metric_id": "cannot-write",
                "platform": "linkedin",
                "captured_at": "2026-09-01T00:00:00Z",
                "metrics": {"views": 1},
            },
            headers=bearer(read_only),
        )
    async with AsyncClient(
        transport=ASGITransport(app=denied_app), base_url="http://test"
    ) as client:
        project_refused = await client.get(
            "/api/v2/content/idempotency-post/metrics",
            params={"profile_id": PROFILE},
            headers=bearer(denied_project),
        )

    assert write_refused.status_code == 403
    assert project_refused.status_code == 403


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
