"""V2 Phase 3 service rules for lifecycle, immutable versions, and ownership."""

from collections.abc import Sequence

import pytest

from app.content.errors import (
    ContentItemNotFoundError,
    InvalidContentLifecycleTransitionError,
)
from app.embeddings.types import EmbeddingVector
from app.models.common import Page, utc_now
from app.models.content import (
    ContentBriefDocument,
    ContentCampaignDocument,
    ContentIdeaDocument,
    ContentItemDocument,
    ContentLinkDocument,
    ContentPublicationProvider,
    ContentVersionDocument,
    CreatorProfileDocument,
)
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.services.content_service import ContentEmbedder, ContentService

PROFILE = "personal-brand"
OTHER_PROFILE = "other-brand"
PROJECT = "payments-api"


class Profiles:
    """In-memory owner lookup for content service tests."""

    def __init__(self) -> None:
        self.records = {
            PROFILE: CreatorProfileDocument(profile_id=PROFILE, name="Personal Brand"),
            OTHER_PROFILE: CreatorProfileDocument(profile_id=OTHER_PROFILE, name="Other Brand"),
        }

    async def get(self, profile_id: str) -> CreatorProfileDocument | None:
        return self.records.get(profile_id)


class Ideas:
    """One profile's idea records."""

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        self.records: dict[str, ContentIdeaDocument] = {}

    async def create(self, document: ContentIdeaDocument) -> ContentIdeaDocument:
        if document.idea_id in self.records:
            raise DuplicateDocumentError("duplicate")
        self.records[document.idea_id] = document
        return document

    async def get(self, idea_id: str) -> ContentIdeaDocument | None:
        return self.records.get(idea_id)

    async def list(
        self, *, limit: int = 20, cursor: str | None = None
    ) -> Page[ContentIdeaDocument]:
        del cursor
        items = tuple(self.records.values())
        return Page(items=items[:limit], next_cursor="ideas-next" if len(items) > limit else None)

    async def update(
        self, document: ContentIdeaDocument, *, expected_revision: int
    ) -> ContentIdeaDocument:
        current = self.records.get(document.idea_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("changed")
        updated = document.model_copy(update={"revision": expected_revision + 1})
        self.records[updated.idea_id] = updated
        return updated


class Briefs:
    """One profile's content briefs."""

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        self.records: dict[str, ContentBriefDocument] = {}

    async def create(self, document: ContentBriefDocument) -> ContentBriefDocument:
        if document.brief_id in self.records:
            raise DuplicateDocumentError("duplicate")
        self.records[document.brief_id] = document
        return document

    async def get(self, brief_id: str) -> ContentBriefDocument | None:
        return self.records.get(brief_id)


class Versions:
    """Append-only snapshots indexed by the content natural key."""

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        self.records: dict[tuple[str, int], ContentVersionDocument] = {}

    async def get(self, content_id: str, version: int) -> ContentVersionDocument | None:
        return self.records.get((content_id, version))

    async def list(
        self, *, content_id: str | None = None, limit: int = 20, cursor: str | None = None
    ) -> Page[ContentVersionDocument]:
        del cursor
        snapshots = tuple(
            document
            for (stored_content_id, _), document in self.records.items()
            if content_id is None or stored_content_id == content_id
        )
        return Page(items=snapshots[:limit])


class Items:
    """Current copies paired atomically with the fake version collection."""

    def __init__(self, profile_id: str, versions: Versions) -> None:
        self.profile_id = profile_id
        self._versions = versions
        self.records: dict[str, ContentItemDocument] = {}

    async def create_with_initial_version(
        self, document: ContentItemDocument, version: ContentVersionDocument
    ) -> ContentItemDocument:
        if document.content_id in self.records:
            raise DuplicateDocumentError("duplicate")
        self.records[document.content_id] = document
        self._versions.records[(version.content_id, version.version)] = version
        return document

    async def get(self, content_id: str) -> ContentItemDocument | None:
        return self.records.get(content_id)

    async def list(
        self, *, limit: int = 20, cursor: str | None = None
    ) -> Page[ContentItemDocument]:
        del cursor
        return Page(items=tuple(self.records.values())[:limit])

    async def update(
        self, document: ContentItemDocument, *, expected_revision: int
    ) -> ContentItemDocument:
        current = self.records.get(document.content_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("changed")
        updated = document.model_copy(update={"revision": expected_revision + 1})
        self.records[updated.content_id] = updated
        return updated

    async def append_version(
        self,
        document: ContentItemDocument,
        version: ContentVersionDocument,
        *,
        expected_revision: int,
    ) -> ContentItemDocument:
        current = self.records.get(document.content_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("changed")
        if (version.content_id, version.version) in self._versions.records:
            raise DuplicateDocumentError("duplicate version")
        self._versions.records[(version.content_id, version.version)] = version
        updated = document.model_copy(update={"revision": expected_revision + 1})
        self.records[updated.content_id] = updated
        return updated


class Links:
    def __init__(self) -> None:
        self.records: dict[str, ContentLinkDocument] = {}

    async def create(self, document: ContentLinkDocument) -> ContentLinkDocument:
        if document.link_id in self.records:
            raise DuplicateDocumentError("duplicate")
        self.records[document.link_id] = document
        return document


class Campaigns:
    def __init__(self) -> None:
        self.records: dict[str, ContentCampaignDocument] = {}

    async def create(self, document: ContentCampaignDocument) -> ContentCampaignDocument:
        if document.campaign_id in self.records:
            raise DuplicateDocumentError("duplicate")
        self.records[document.campaign_id] = document
        return document


class ContentFakes:
    """Factories that retain separate storage for each creator profile."""

    def __init__(self) -> None:
        self.profiles = Profiles()
        self.ideas: dict[str, Ideas] = {}
        self.briefs: dict[str, Briefs] = {}
        self.versions: dict[str, Versions] = {}
        self.items: dict[str, Items] = {}
        self.links: dict[str, Links] = {}
        self.campaigns: dict[str, Campaigns] = {}

    def service(self, embedder: ContentEmbedder | None = None) -> ContentService:
        return ContentService(
            self.profiles,
            lambda profile: self.ideas.setdefault(profile, Ideas(profile)),
            lambda profile: self.briefs.setdefault(profile, Briefs(profile)),
            lambda profile: self.items.setdefault(
                profile,
                Items(profile, self.versions.setdefault(profile, Versions(profile))),
            ),
            lambda profile: self.versions.setdefault(profile, Versions(profile)),
            lambda profile: self.links.setdefault(profile, Links()),
            lambda profile: self.campaigns.setdefault(profile, Campaigns()),
            embedder,
        )


class RecordingEmbedder:
    """The local embedding contract fake used to prove Phase 4 write indexing."""

    def __init__(self) -> None:
        self.contents: list[str] = []

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        self.contents.extend(contents)
        return tuple(
            EmbeddingVector(model="local-test", dimensions=2, values=(0.5, 0.5)) for _ in contents
        )


async def create_idea_and_brief(service: ContentService) -> None:
    await service.create_idea(
        profile_id=PROFILE,
        idea_id="idempotency",
        title="Why idempotency matters",
        description="A practical explanation of safe API retries.",
        pillar_id="payments",
        platforms=["linkedin"],
        origin={"type": "project_context", "project_id": PROJECT, "source_ids": ["retry-rule"]},
        tags=["payments"],
        priority=0.9,
    )
    await service.create_brief(
        profile_id=PROFILE,
        brief_id="idempotency-linkedin",
        idea_id="idempotency",
        platform="linkedin",
        objective="Explain idempotent payment API retries.",
        audience=["backend engineers"],
        primary_message="Idempotency makes retries safe.",
        supporting_points=["Store and replay the result."],
        source_refs=[
            {"source_type": "project_context", "source_id": "retry-rule", "project_id": PROJECT}
        ],
        skills=["linkedin-writing"],
        avoid_repetition_with=[],
        tags=["payments"],
    )


async def create_draft(service: ContentService) -> ContentItemDocument:
    return await service.create_content(
        profile_id=PROFILE,
        content_id="idempotency-post",
        brief_id="idempotency-linkedin",
        platform="linkedin",
        content_type="post",
        title="Why idempotency matters",
        body="First draft about safe retries.",
        topic_tags=["idempotency"],
        pillar_id="payments",
        project_ids=[PROJECT],
        source_refs=[
            {"source_type": "project_context", "source_id": "retry-rule", "project_id": PROJECT}
        ],
        tags=["payments"],
        created_by="editor",
    )


@pytest.mark.asyncio
async def test_content_lifecycle_versions_and_rollback_preserve_provenance() -> None:
    fakes = ContentFakes()
    service = fakes.service()
    await create_idea_and_brief(service)
    draft = await create_draft(service)

    assert draft.status == "drafting"
    assert draft.current_version == 1
    assert fakes.versions[PROFILE].records[(draft.content_id, 1)].body == draft.body

    review = await service.update_content(
        PROFILE,
        draft.content_id,
        expected_revision=draft.revision,
        changes={"status": "review"},
    )
    approved = await service.update_content(
        PROFILE,
        draft.content_id,
        expected_revision=review.revision,
        changes={"status": "approved"},
    )
    published = await service.mark_content_published(
        PROFILE,
        draft.content_id,
        expected_revision=approved.revision,
        provider=ContentPublicationProvider.MANUAL,
        external_id=None,
        external_url="https://example.test/post",
        published_at=utc_now(),
        idempotency_key="manual-publish-1",
    )
    assert published.published_at is not None
    with pytest.raises(InvalidContentLifecycleTransitionError):
        await service.update_content(
            PROFILE,
            draft.content_id,
            expected_revision=published.revision,
            changes={"status": "drafting"},
        )
    revised = await service.create_version(
        PROFILE,
        draft.content_id,
        expected_revision=published.revision,
        body="Second draft after publication review.",
        change_reason="clarify retry behavior",
        created_by="editor",
    )
    assert revised.status == "drafting"
    assert revised.current_version == 2
    assert revised.published_at is None
    assert fakes.versions[PROFILE].records[(draft.content_id, 1)].body == draft.body

    rolled_back = await service.rollback_content(
        PROFILE,
        draft.content_id,
        expected_revision=revised.revision,
        rollback_to_version=1,
        change_reason="restore initial wording",
        created_by="editor",
    )
    assert rolled_back.current_version == 3
    assert rolled_back.body == draft.body
    assert rolled_back.source_refs == draft.source_refs
    assert fakes.versions[PROFILE].records[(draft.content_id, 2)].body.startswith("Second")
    assert fakes.versions[PROFILE].records[(draft.content_id, 3)].body == draft.body
    archived = await service.update_content(
        PROFILE,
        draft.content_id,
        expected_revision=rolled_back.revision,
        changes={"status": "archived"},
    )
    assert archived.status == "archived"
    assert (await service.list_content(PROFILE)).items == (archived,)


@pytest.mark.asyncio
async def test_current_content_representations_are_embedded_but_versions_are_not() -> None:
    fakes = ContentFakes()
    embedder = RecordingEmbedder()
    service = fakes.service(embedder)

    await create_idea_and_brief(service)
    draft = await create_draft(service)
    revised = await service.create_version(
        PROFILE,
        draft.content_id,
        expected_revision=draft.revision,
        body="A revised current draft about payment retries and idempotency.",
        change_reason="clarify the retry boundary",
        created_by="editor",
    )
    archived = await service.update_content(
        PROFILE,
        revised.content_id,
        expected_revision=revised.revision,
        changes={"status": "archived"},
    )

    stored_idea = fakes.ideas[PROFILE].records["idempotency"]
    stored_brief = fakes.briefs[PROFILE].records["idempotency-linkedin"]
    stored_version = fakes.versions[PROFILE].records[(draft.content_id, 2)]
    assert stored_idea.embedding_model == "local-test"
    assert stored_brief.embedding_model == "local-test"
    assert revised.embedding_model == "local-test"
    assert archived.embedding is None
    assert not hasattr(stored_version, "embedding")
    # Idea, brief, initial current draft, and replacement current draft only.
    assert len(embedder.contents) == 4


@pytest.mark.asyncio
async def test_idea_lifecycle_cursor_pagination_and_profile_isolation() -> None:
    fakes = ContentFakes()
    service = fakes.service()
    for suffix in ("one", "two"):
        await service.create_idea(
            profile_id=PROFILE,
            idea_id=f"idea-{suffix}",
            title=f"Idea {suffix}",
            description="A manually supplied technical content idea.",
            pillar_id=None,
            platforms=["medium"],
            origin={"type": "memory"},
            tags=[],
            priority=0.5,
        )
    page = await service.list_ideas(PROFILE, limit=1, cursor="later")
    assert len(page.items) == 1
    assert page.next_cursor == "ideas-next"
    with pytest.raises(InvalidContentLifecycleTransitionError):
        await service.update_idea(
            PROFILE,
            "idea-one",
            expected_revision=1,
            changes={"status": "published"},
        )
    with pytest.raises(ContentItemNotFoundError):
        await service.get_content(OTHER_PROFILE, "idempotency-post")


@pytest.mark.asyncio
async def test_content_link_and_campaign_require_profile_owned_content() -> None:
    fakes = ContentFakes()
    service = fakes.service()
    await create_idea_and_brief(service)
    draft = await create_draft(service)
    link = await service.create_link(
        profile_id=PROFILE,
        link_id="retry-rule-derived",
        content_id=draft.content_id,
        source_type="project_context",
        source_id="retry-rule",
        project_id=PROJECT,
        relation="derived_from",
        tags=["provenance"],
    )
    campaign = await service.create_campaign(
        profile_id=PROFILE,
        campaign_id="payments-series",
        name="Payments Series",
        description="A short content series about reliable payments.",
        content_ids=[draft.content_id],
        platforms=["linkedin"],
        tags=["payments"],
        status="planned",
    )
    assert link.content_id == draft.content_id
    assert campaign.content_ids == [draft.content_id]
