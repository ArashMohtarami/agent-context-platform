"""Phase 5 deterministic idea discovery and brief-preparation behavior."""

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.content.discovery import DiscoveryTimeRange
from app.models.common import Page
from app.models.content import (
    ContentIdeaDocument,
    ContentItemDocument,
    ContentPillarDocument,
    ContentSourceReference,
    CreatorProfileDocument,
)
from app.models.memory import MemoryDocument
from app.models.project import ProjectDocument
from app.models.project_context import ProjectContextDocument
from app.models.skill import SkillDocument
from app.services.content_discovery_service import ContentDiscoveryService

PROFILE = "personal-brand"
PROJECT = "payment-service"
NOW = datetime(2026, 9, 22, tzinfo=UTC)


def profile() -> CreatorProfileDocument:
    return CreatorProfileDocument(
        profile_id=PROFILE,
        name="Technical Brand",
        audiences=["backend engineers"],
        domains=["payments", "distributed systems"],
        topics_of_interest=["payments"],
        voice={"tone": ["practical"], "avoid": ["hype"]},
    )


def project() -> ProjectDocument:
    return ProjectDocument(
        project_id=PROJECT,
        name="Payment Service",
        description="Handles reliable payments.",
        repository="https://example.test/payment-service",
        environment="production",
    )


def pillar() -> ContentPillarDocument:
    return ContentPillarDocument(
        profile_id=PROFILE,
        pillar_id="payments",
        name="Payments",
        description="Reliable payment architecture.",
        tags=["payments", "outbox"],
        priority=0.9,
    )


def context(
    *,
    title: str = "Outbox decision",
    content: str = "Persist payment events in the same transaction before delivery.",
    tags: list[str] | None = None,
    updated_at: datetime = NOW,
) -> ProjectContextDocument:
    return ProjectContextDocument(
        project_id=PROJECT,
        type="architecture_decision",
        title=title,
        content=content,
        importance=0.9,
        source="adr",
        tags=["payments", "outbox"] if tags is None else tags,
        created_at=updated_at,
        updated_at=updated_at,
    )


def memory(
    *,
    project_id: str | None = PROJECT,
    summary: str = "Idempotent payment retries",
    updated_at: datetime = NOW,
) -> MemoryDocument:
    return MemoryDocument(
        scope="global" if project_id is None else "project",
        project_id=project_id,
        type="lesson",
        content="A retry must reuse the first payment result.",
        summary=summary,
        importance=0.8,
        confidence=0.9,
        source="retrospective",
        tags=["payments", "idempotency"],
        created_at=updated_at,
        updated_at=updated_at,
    )


def skill(skill_id: str, *, trusted: bool = True) -> SkillDocument:
    return SkillDocument(
        skill_id=skill_id,
        name=skill_id.replace("-", " ").title(),
        version="1.0.0",
        description="Payment transaction implementation guidance.",
        category="content" if skill_id.endswith("writing") else "architecture",
        tags=["payments"],
        trust_level="trusted" if trusted else "external",
        source_path=f"skills/{skill_id}.md",
        content_hash="a" * 64,
        created_at=NOW,
        updated_at=NOW,
    )


def published() -> ContentItemDocument:
    return ContentItemDocument(
        profile_id=PROFILE,
        content_id="outbox-post",
        platform="linkedin",
        content_type="post",
        title="The Outbox lesson",
        body="A published payment outbox explanation.",
        status="published",
        topic_tags=["outbox"],
        pillar_id="payments",
        project_ids=[PROJECT],
        source_refs=[
            {"source_type": "project_context", "source_id": "outbox-adr", "project_id": PROJECT}
        ],
        tags=["payments"],
        published_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


class Profiles:
    async def get(self, profile_id: str) -> CreatorProfileDocument | None:
        return profile() if profile_id == PROFILE else None


class Projects:
    async def get(self, project_id: str) -> ProjectDocument | None:
        return project() if project_id == PROJECT else None


class Pillars:
    async def get(self, pillar_id: str) -> ContentPillarDocument | None:
        return pillar() if pillar_id == "payments" else None

    async def list(
        self, *, limit: int = 20, cursor: str | None = None
    ) -> Page[ContentPillarDocument]:
        return Page(items=(pillar(),), next_cursor=None)


class Ideas:
    def __init__(self, ideas: tuple[ContentIdeaDocument, ...] = ()) -> None:
        self.items = ideas

    async def get(self, idea_id: str) -> ContentIdeaDocument | None:
        return next((item for item in self.items if item.idea_id == idea_id), None)

    async def list_recent(
        self,
        *,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int = 50,
    ) -> tuple[ContentIdeaDocument, ...]:
        return self.items[:limit]


class Items:
    def __init__(self, items: tuple[ContentItemDocument, ...] = ()) -> None:
        self.items = items

    async def get(self, content_id: str) -> ContentItemDocument | None:
        return next((item for item in self.items if item.content_id == content_id), None)

    async def list_recent(self, **_: object) -> tuple[ContentItemDocument, ...]:
        return self.items


class Contexts:
    def __init__(self, documents: tuple[ProjectContextDocument, ...]) -> None:
        self.documents = documents
        self.calls: list[tuple[datetime | None, datetime | None]] = []

    async def get(self, document_id: ObjectId) -> ProjectContextDocument | None:
        return next((item for item in self.documents if item.id == document_id), None)

    async def list_recent(
        self,
        *,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        limit: int = 50,
    ) -> tuple[ProjectContextDocument, ...]:
        self.calls.append((updated_after, updated_before))
        return tuple(
            item
            for item in self.documents
            if (updated_after is None or item.updated_at >= updated_after)
            and (updated_before is None or item.updated_at <= updated_before)
        )[:limit]


class Memories:
    def __init__(self, documents: tuple[MemoryDocument, ...] = ()) -> None:
        self.documents = documents

    async def get(self, document_id: ObjectId) -> MemoryDocument | None:
        return next((item for item in self.documents if item.id == document_id), None)

    async def list_recent(self, **_: object) -> tuple[MemoryDocument, ...]:
        return self.documents


class Skills:
    def __init__(self, documents: tuple[SkillDocument, ...]) -> None:
        self.documents = documents

    async def get_active(self, skill_id: str) -> SkillDocument | None:
        return next((item for item in self.documents if item.skill_id == skill_id), None)

    async def list_recent(self, **_: object) -> tuple[SkillDocument, ...]:
        return self.documents


def discovery_service(
    *,
    contexts: tuple[ProjectContextDocument, ...] = (context(),),
    ideas: tuple[ContentIdeaDocument, ...] = (),
    items: tuple[ContentItemDocument, ...] = (published(),),
    project_memories: tuple[MemoryDocument, ...] = (memory(),),
    global_memories: tuple[MemoryDocument, ...] = (),
) -> tuple[ContentDiscoveryService, Contexts]:
    context_repository = Contexts(contexts)
    idea_repository = Ideas(ideas)
    item_repository = Items(items)
    service = ContentDiscoveryService(
        Profiles(),
        Projects(),
        lambda _profile: Pillars(),
        lambda _profile: idea_repository,
        lambda _profile: item_repository,
        lambda _project: context_repository,
        lambda project_id: Memories(project_memories if project_id == PROJECT else global_memories),
        Skills((skill("payment-architecture"), skill("linkedin-writing"), skill("medium-writing"))),
        clock=lambda: NOW,
    )
    return service, context_repository


@pytest.mark.asyncio
async def test_discovers_recent_project_evidence_with_source_provenance_and_platform() -> None:
    service, repository = discovery_service()

    result = await service.discover_content_ideas(
        profile_id=PROFILE,
        project_id=PROJECT,
        pillar_id="payments",
        platform="linkedin",
        time_range=DiscoveryTimeRange(start_at=NOW - timedelta(days=1)),
        limit=10,
    )

    candidate = next(item for item in result.candidates if item.topic == "Outbox decision")
    assert candidate.suggested_platforms == ("linkedin",)
    assert candidate.related_project_id == PROJECT
    assert candidate.related_pillar_id == "payments"
    assert candidate.source_refs[0].source_id == str(repository.documents[0].id)
    assert candidate.previous_similar_content[0].content_id == "outbox-post"
    assert repository.calls == [(NOW - timedelta(days=1), None)]


@pytest.mark.asyncio
async def test_pillar_filter_and_exact_unpublished_duplicate_handling_are_deterministic() -> None:
    duplicate = ContentIdeaDocument(
        profile_id=PROFILE,
        idea_id="outbox-existing",
        title="Outbox decision",
        description="Existing reviewed idea.",
        pillar_id="payments",
        platforms=["linkedin"],
        origin={"type": "project_context", "project_id": PROJECT, "source_ids": ["source"]},
        priority=0.9,
    )
    service, _ = discovery_service(
        contexts=(context(), context(title="Security rotation decision")), ideas=(duplicate,)
    )

    result = await service.discover_content_ideas(
        profile_id=PROFILE, project_id=PROJECT, pillar_id="payments"
    )

    assert all(candidate.topic != "Outbox decision" for candidate in result.candidates)
    assert [item.idea_id for item in result.existing_unpublished_ideas] == ["outbox-existing"]
    assert all(candidate.related_pillar_id == "payments" for candidate in result.candidates)


@pytest.mark.asyncio
async def test_discovery_does_not_offer_unrelated_project_context_without_a_pillar_filter() -> None:
    service, _ = discovery_service(
        contexts=(
            context(),
            context(
                title="Frontend color palette",
                content="CSS color tokens for marketing pages.",
                tags=["frontend", "css"],
            ),
        )
    )

    result = await service.discover_content_ideas(profile_id=PROFILE, project_id=PROJECT)

    assert {candidate.topic for candidate in result.candidates} >= {"Outbox decision"}
    assert "Frontend color palette" not in {candidate.topic for candidate in result.candidates}


@pytest.mark.asyncio
async def test_discovery_keeps_global_and_project_source_ids_in_separate_saved_origins() -> None:
    project_memory = memory(summary="Payment retry lesson")
    global_memory = memory(project_id=None, summary="Payment retry lesson")
    service, _ = discovery_service(
        contexts=(),
        items=(),
        project_memories=(project_memory,),
        global_memories=(global_memory,),
    )

    result = await service.discover_content_ideas(profile_id=PROFILE, project_id=PROJECT)

    matches = [
        candidate for candidate in result.candidates if candidate.topic == "Payment retry lesson"
    ]
    assert len(matches) == 2
    assert {candidate.source_refs[0].project_id for candidate in matches} == {None, PROJECT}


@pytest.mark.asyncio
async def test_prepare_brief_keeps_idea_provenance_and_selects_trusted_platform_skills() -> None:
    source = context()
    idea = ContentIdeaDocument(
        profile_id=PROFILE,
        idea_id="outbox-idea",
        title="Outbox decision",
        description="Explain the transaction boundary behind reliable event delivery.",
        pillar_id="payments",
        platforms=["linkedin", "medium"],
        origin={"type": "project_context", "project_id": PROJECT, "source_ids": [str(source.id)]},
        priority=0.9,
    )
    service, _ = discovery_service(contexts=(source,), ideas=(idea,))

    brief = await service.prepare_content_brief(
        profile_id=PROFILE,
        idea_id="outbox-idea",
        platform="linkedin",
    )

    assert brief.source_refs == (
        ContentSourceReference(
            source_type="project_context", source_id=str(source.id), project_id=PROJECT
        ),
    )
    assert brief.supporting_points == ("Outbox decision",)
    assert [item.skill_id for item in brief.technical_skills] == ["payment-architecture"]
    assert brief.content_skill is not None
    assert brief.content_skill.skill_id == "linkedin-writing"
    assert "do not fabricate project experiences" in brief.content_constraints


@pytest.mark.asyncio
async def test_discovery_returns_an_empty_result_without_project_context_or_matching_sources() -> (
    None
):
    service, _ = discovery_service(contexts=(), items=())

    result = await service.discover_content_ideas(
        profile_id=PROFILE,
        project_id=PROJECT,
        pillar_id="payments",
        time_range=DiscoveryTimeRange(start_at=NOW + timedelta(days=1)),
    )

    assert result.candidates == ()
