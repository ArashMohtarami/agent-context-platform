"""V2 Phase 3 content storage, lifecycle, and immutable-version rules.

The service stores creator content as data.  It never generates prose, chooses
sources, retrieves private context, or publishes to an external platform.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Protocol, cast

from app.content.errors import (
    ContentBriefAlreadyExistsError,
    ContentBriefNotFoundError,
    ContentCampaignAlreadyExistsError,
    ContentIdeaAlreadyExistsError,
    ContentIdeaNotFoundError,
    ContentItemAlreadyExistsError,
    ContentItemNotFoundError,
    ContentLinkAlreadyExistsError,
    ContentVersionConflictError,
    ContentVersionNotFoundError,
    InvalidContentLifecycleTransitionError,
)
from app.creator.errors import CreatorProfileNotFoundError
from app.embeddings.errors import EmbeddingCompatibilityError
from app.embeddings.types import EmbeddingVector
from app.models.common import Page, utc_now
from app.models.content import (
    ContentBriefDocument,
    ContentBriefStatus,
    ContentCampaignDocument,
    ContentCampaignStatus,
    ContentIdeaDocument,
    ContentItemDocument,
    ContentLinkDocument,
    ContentOrigin,
    ContentPlatform,
    ContentPublicationProvider,
    ContentRelationType,
    ContentSourceReference,
    ContentStatus,
    ContentVersionDocument,
    CreatorProfileDocument,
    CreatorProfileStatus,
)
from app.models.research import ResearchBriefCitationReference, ResearchBriefClaimReference
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.security.tenant_service import tenant_service
from app.services.content_research_service import ContentResearchIntegrationService


class CreatorProfileRepository(Protocol):
    """The profile lookup required to retain the V2 ownership boundary."""

    async def get(self, profile_id: str) -> CreatorProfileDocument | None: ...


class ContentIdeaRepository(Protocol):
    """Profile-isolated idea persistence used by lifecycle operations."""

    async def create(self, document: ContentIdeaDocument) -> ContentIdeaDocument: ...

    async def get(self, idea_id: str) -> ContentIdeaDocument | None: ...

    async def list(
        self, *, limit: int = ..., cursor: str | None = ...
    ) -> Page[ContentIdeaDocument]: ...

    async def update(
        self, document: ContentIdeaDocument, *, expected_revision: int
    ) -> ContentIdeaDocument: ...


class ContentBriefRepository(Protocol):
    """Profile-isolated brief persistence."""

    async def create(self, document: ContentBriefDocument) -> ContentBriefDocument: ...

    async def get(self, brief_id: str) -> ContentBriefDocument | None: ...


class ContentItemRepository(Protocol):
    """Current content and atomic immutable-history persistence."""

    async def create_with_initial_version(
        self, document: ContentItemDocument, version: ContentVersionDocument
    ) -> ContentItemDocument: ...

    async def get(self, content_id: str) -> ContentItemDocument | None: ...

    async def list(
        self, *, limit: int = ..., cursor: str | None = ...
    ) -> Page[ContentItemDocument]: ...

    async def update(
        self, document: ContentItemDocument, *, expected_revision: int
    ) -> ContentItemDocument: ...

    async def append_version(
        self,
        document: ContentItemDocument,
        version: ContentVersionDocument,
        *,
        expected_revision: int,
    ) -> ContentItemDocument: ...


class ContentVersionRepository(Protocol):
    """Read-only access to append-only content snapshots."""

    async def get(self, content_id: str, version: int) -> ContentVersionDocument | None: ...

    async def list(
        self, *, content_id: str | None = ..., limit: int = ..., cursor: str | None = ...
    ) -> Page[ContentVersionDocument]: ...


class ContentLinkRepository(Protocol):
    """Durable provenance-link persistence."""

    async def create(self, document: ContentLinkDocument) -> ContentLinkDocument: ...


class ContentCampaignRepository(Protocol):
    """Profile-owned campaign metadata persistence."""

    async def create(self, document: ContentCampaignDocument) -> ContentCampaignDocument: ...


class ContentEmbedder(Protocol):
    """The existing local embedding service surface used for current content."""

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]: ...


_EMBEDDABLE_ITEM_STATUSES = frozenset(
    {ContentStatus.DRAFTING, ContentStatus.APPROVED, ContentStatus.PUBLISHED}
)


def idea_embedding_text(document: ContentIdeaDocument) -> str:
    """Return the current semantic representation of an idea, not its origin data."""
    return "\n\n".join(
        part
        for part in (
            document.title,
            document.description,
            "Tags: " + ", ".join(document.tags) if document.tags else "",
        )
        if part
    )


def brief_embedding_text(document: ContentBriefDocument) -> str:
    """Return the current semantic representation of a brief without source payloads."""
    return "\n\n".join(
        part
        for part in (
            document.objective,
            document.primary_message,
            "\n".join(document.supporting_points),
            "Tags: " + ", ".join(document.tags) if document.tags else "",
        )
        if part
    )


def _merge_source_refs(
    existing: Sequence[ContentSourceReference],
    additions: Sequence[ContentSourceReference],
) -> list[ContentSourceReference]:
    """Retain caller ordering while adding each V3 project-scope reference once."""
    merged: list[ContentSourceReference] = []
    seen: set[tuple[str, str, str | None]] = set()
    for reference in (*existing, *additions):
        key = (reference.source_type, reference.source_id, reference.project_id)
        if key not in seen:
            seen.add(key)
            merged.append(reference)
    return merged


def item_embedding_text(document: ContentItemDocument) -> str:
    """Return the current content representation; historical versions are never embedded."""
    return "\n\n".join(
        part
        for part in (
            document.title,
            document.body,
            "Tags: " + ", ".join((*document.topic_tags, *document.tags))
            if document.topic_tags or document.tags
            else "",
        )
        if part
    )


# A direct state mutation never makes a published item a draft.  Editing a
# published item is deliberately routed through `create_version`, which both
# leaves its published snapshot intact and records who revised it.
_ALLOWED_CONTENT_TRANSITIONS: dict[ContentStatus, frozenset[ContentStatus]] = {
    ContentStatus.IDEA: frozenset({ContentStatus.SELECTED, ContentStatus.ARCHIVED}),
    ContentStatus.SELECTED: frozenset(
        {ContentStatus.IDEA, ContentStatus.RESEARCHING, ContentStatus.ARCHIVED}
    ),
    ContentStatus.RESEARCHING: frozenset(
        {ContentStatus.SELECTED, ContentStatus.BRIEF_READY, ContentStatus.ARCHIVED}
    ),
    ContentStatus.BRIEF_READY: frozenset(
        {ContentStatus.RESEARCHING, ContentStatus.DRAFTING, ContentStatus.ARCHIVED}
    ),
    ContentStatus.DRAFTING: frozenset({ContentStatus.REVIEW, ContentStatus.ARCHIVED}),
    ContentStatus.REVIEW: frozenset(
        {ContentStatus.DRAFTING, ContentStatus.APPROVED, ContentStatus.ARCHIVED}
    ),
    ContentStatus.APPROVED: frozenset(
        {
            ContentStatus.DRAFTING,
            ContentStatus.REVIEW,
            ContentStatus.PUBLISHED,
            ContentStatus.ARCHIVED,
        }
    ),
    ContentStatus.PUBLISHED: frozenset({ContentStatus.ARCHIVED}),
    ContentStatus.ARCHIVED: frozenset(),
}


@tenant_service
class ContentService:
    """Coordinate profile-isolated content records without generation or publishing."""

    def __init__(
        self,
        profiles: CreatorProfileRepository,
        ideas: Callable[[str], ContentIdeaRepository],
        briefs: Callable[[str], ContentBriefRepository],
        items: Callable[[str], ContentItemRepository],
        versions: Callable[[str], ContentVersionRepository],
        links: Callable[[str], ContentLinkRepository],
        campaigns: Callable[[str], ContentCampaignRepository],
        embedder: ContentEmbedder | None = None,
        research: ContentResearchIntegrationService | None = None,
    ) -> None:
        self._profiles = profiles
        self._ideas = ideas
        self._briefs = briefs
        self._items = items
        self._versions = versions
        self._links = links
        self._campaigns = campaigns
        self._embedder = embedder
        self._research = research

    async def get_profile(self, profile_id: str) -> CreatorProfileDocument:
        """Load an existing owner without weakening caller authorization."""
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise CreatorProfileNotFoundError("creator profile was not found")
        return profile

    async def _require_active_profile(self, profile_id: str) -> None:
        profile = await self.get_profile(profile_id)
        if profile.status is not CreatorProfileStatus.ACTIVE:
            raise InvalidContentLifecycleTransitionError(
                "inactive creator profiles cannot create or change content"
            )

    async def create_idea(
        self,
        *,
        profile_id: str,
        idea_id: str,
        title: str,
        description: str,
        pillar_id: str | None,
        platforms: Sequence[ContentPlatform],
        origin: ContentOrigin,
        tags: Sequence[str],
        priority: float,
        status: ContentStatus = ContentStatus.IDEA,
    ) -> ContentIdeaDocument:
        """Store a new manually supplied idea as the first lifecycle state."""
        await self._require_active_profile(profile_id)
        if status is not ContentStatus.IDEA:
            raise InvalidContentLifecycleTransitionError("new content ideas must start as idea")
        document = ContentIdeaDocument(
            profile_id=profile_id,
            idea_id=idea_id,
            title=title,
            description=description,
            pillar_id=pillar_id,
            platforms=list(platforms),
            origin=origin,
            tags=list(tags),
            priority=priority,
            status=status,
        )
        document = await self._embedded_idea(document)
        try:
            return await self._ideas(profile_id).create(document)
        except DuplicateDocumentError as error:
            raise ContentIdeaAlreadyExistsError("content idea already exists") from error

    async def get_idea(self, profile_id: str, idea_id: str) -> ContentIdeaDocument:
        """Read one idea inside its owning profile."""
        await self.get_profile(profile_id)
        idea = await self._ideas(profile_id).get(idea_id)
        if idea is None:
            raise ContentIdeaNotFoundError("content idea was not found")
        return idea

    async def list_ideas(
        self, profile_id: str, *, limit: int = 20, cursor: str | None = None
    ) -> Page[ContentIdeaDocument]:
        """Return a cursor page without crossing the profile boundary."""
        await self.get_profile(profile_id)
        return await self._ideas(profile_id).list(limit=limit, cursor=cursor)

    async def update_idea(
        self,
        profile_id: str,
        idea_id: str,
        *,
        expected_revision: int,
        changes: Mapping[str, object],
    ) -> ContentIdeaDocument:
        """Revision-guard idea metadata and lifecycle state transitions."""
        await self._require_active_profile(profile_id)
        current = await self.get_idea(profile_id, idea_id)
        if current.revision != expected_revision:
            raise ConcurrencyConflictError("content idea changed after it was read")
        if changes.get("status") in {ContentStatus.APPROVED, ContentStatus.APPROVED.value}:
            from app.security.policy.reviews import require_review

            require_review(current.model_dump(), "content_items", "content:write")
        _validate_status_change(current.status, changes.get("status"))
        payload = current.model_dump(by_alias=True, mode="python")
        payload.update(changes)
        updated = ContentIdeaDocument.model_validate(payload, context={"from_storage": True})
        updated = await self._embedded_idea(updated)
        return await self._ideas(profile_id).update(updated, expected_revision=expected_revision)

    async def create_brief(
        self,
        *,
        profile_id: str,
        brief_id: str,
        idea_id: str,
        platform: ContentPlatform,
        objective: str,
        audience: Sequence[str],
        primary_message: str,
        supporting_points: Sequence[str],
        source_refs: Sequence[ContentSourceReference],
        skills: Sequence[str],
        avoid_repetition_with: Sequence[str],
        tags: Sequence[str],
        status: ContentBriefStatus = ContentBriefStatus.DRAFT,
        research_project_id: str | None = None,
        claim_refs: Sequence[ResearchBriefClaimReference] = (),
        citation_refs: Sequence[ResearchBriefCitationReference] = (),
        allowed_project_ids: tuple[str, ...] | None = None,
    ) -> ContentBriefDocument:
        """Store a reviewed evidence plan without creating or changing research.

        V3 references are re-resolved from their authoritative records before
        this V2 brief is stored. The operation copies provenance only; it does
        not turn research into memory, project context, a skill, or draft prose.
        """
        await self._require_active_profile(profile_id)
        await self.get_idea(profile_id, idea_id)
        resolved_claim_refs = list(claim_refs)
        resolved_citation_refs = list(citation_refs)
        resolved_source_refs = list(source_refs)
        if research_project_id is not None:
            if self._research is None:
                raise ValueError("research-backed briefs are not configured")
            material = await self._research.materialize_brief_references(
                research_project_id=research_project_id,
                claim_refs=claim_refs,
                citation_refs=citation_refs,
                allowed_project_ids=allowed_project_ids,
            )
            resolved_claim_refs = list(material.claim_refs)
            resolved_citation_refs = list(material.citation_refs)
            resolved_source_refs = _merge_source_refs(resolved_source_refs, material.source_refs)
        document = ContentBriefDocument(
            profile_id=profile_id,
            brief_id=brief_id,
            idea_id=idea_id,
            platform=platform,
            objective=objective,
            audience=list(audience),
            primary_message=primary_message,
            supporting_points=list(supporting_points),
            source_refs=resolved_source_refs,
            research_project_id=research_project_id,
            claim_refs=resolved_claim_refs,
            citation_refs=resolved_citation_refs,
            skills=list(skills),
            avoid_repetition_with=list(avoid_repetition_with),
            tags=list(tags),
            status=status,
        )
        document = await self._embedded_brief(document)
        try:
            return await self._briefs(profile_id).create(document)
        except DuplicateDocumentError as error:
            raise ContentBriefAlreadyExistsError("content brief already exists") from error

    async def get_brief(self, profile_id: str, brief_id: str) -> ContentBriefDocument:
        """Read one brief inside its owner boundary."""
        await self.get_profile(profile_id)
        brief = await self._briefs(profile_id).get(brief_id)
        if brief is None:
            raise ContentBriefNotFoundError("content brief was not found")
        return brief

    async def create_content(
        self,
        *,
        profile_id: str,
        content_id: str,
        brief_id: str | None,
        platform: ContentPlatform,
        content_type: str,
        title: str,
        body: str,
        topic_tags: Sequence[str],
        pillar_id: str | None,
        project_ids: Sequence[str],
        source_refs: Sequence[ContentSourceReference],
        tags: Sequence[str],
        created_by: str | None,
    ) -> ContentItemDocument:
        """Create a draft and immutable version 1 in one atomic write."""
        await self._require_active_profile(profile_id)
        if brief_id is not None:
            brief = await self.get_brief(profile_id, brief_id)
            if brief.platform != platform:
                raise ValueError("content platform must match its brief platform")
        item = ContentItemDocument(
            profile_id=profile_id,
            content_id=content_id,
            brief_id=brief_id,
            platform=platform,
            content_type=content_type,
            title=title,
            body=body,
            status=ContentStatus.DRAFTING,
            topic_tags=list(topic_tags),
            pillar_id=pillar_id,
            project_ids=list(project_ids),
            source_refs=list(source_refs),
            tags=list(tags),
        )
        item = await self._embedded_item(item)
        initial_version = ContentVersionDocument(
            profile_id=profile_id,
            content_id=content_id,
            version=1,
            title=title,
            body=body,
            status=ContentStatus.DRAFTING,
            change_reason="initial draft",
            created_by=created_by,
            tags=list(tags),
        )
        try:
            return await self._items(profile_id).create_with_initial_version(item, initial_version)
        except DuplicateDocumentError as error:
            raise ContentItemAlreadyExistsError("content item already exists") from error

    async def get_content(self, profile_id: str, content_id: str) -> ContentItemDocument:
        """Read a current item, including archived records, within its profile."""
        await self.get_profile(profile_id)
        item = await self._items(profile_id).get(content_id)
        if item is None:
            raise ContentItemNotFoundError("content item was not found")
        return item

    async def list_content(
        self, profile_id: str, *, limit: int = 20, cursor: str | None = None
    ) -> Page[ContentItemDocument]:
        """Page all current copies, including archived items for later retrieval."""
        await self.get_profile(profile_id)
        return await self._items(profile_id).list(limit=limit, cursor=cursor)

    async def update_content(
        self,
        profile_id: str,
        content_id: str,
        *,
        expected_revision: int,
        changes: Mapping[str, object],
    ) -> ContentItemDocument:
        """Change metadata or lifecycle state, never a body snapshot directly."""
        await self._require_active_profile(profile_id)
        if "body" in changes or "current_version" in changes:
            raise ValueError("content body changes require a new content version")
        protected_publication_fields = {
            "published_at",
            "external_url",
            "publication_provider",
            "external_id",
            "publication_idempotency_key",
        }
        if protected_publication_fields & changes.keys():
            raise InvalidContentLifecycleTransitionError(
                "publication metadata requires an explicit publish action"
            )
        if changes.get("status") in {ContentStatus.PUBLISHED, ContentStatus.PUBLISHED.value}:
            raise InvalidContentLifecycleTransitionError(
                "content must be published through the explicit publish action"
            )
        current = await self.get_content(profile_id, content_id)
        if current.revision != expected_revision:
            raise ConcurrencyConflictError("content item changed after it was read")
        changes = dict(changes)
        authorial = {"title", "topic_tags", "pillar_id", "project_ids", "source_refs", "tags"}
        edited = any(current.model_dump().get(k) != v for k, v in changes.items() if k in authorial)
        if edited:
            if changes.get("status") in {ContentStatus.APPROVED, ContentStatus.APPROVED.value}:
                raise InvalidContentLifecycleTransitionError("editing cannot also approve content")
            if current.status is ContentStatus.PUBLISHED:
                raise InvalidContentLifecycleTransitionError(
                    "published content requires a new draft version"
                )
            if current.status in {ContentStatus.REVIEW, ContentStatus.APPROVED}:
                changes["status"] = ContentStatus.DRAFTING
        if changes.get("status") in {ContentStatus.APPROVED, ContentStatus.APPROVED.value}:
            from app.security.policy.reviews import require_review

            require_review(current.model_dump(), "content_items", "content:write")
        _validate_status_change(current.status, changes.get("status"))
        payload = current.model_dump(by_alias=True, mode="python")
        payload.update(changes)
        updated = ContentItemDocument.model_validate(payload, context={"from_storage": True})
        updated = await self._embedded_item(updated)
        return await self._items(profile_id).update(updated, expected_revision=expected_revision)

    async def mark_content_published(
        self,
        profile_id: str,
        content_id: str,
        *,
        expected_revision: int,
        provider: ContentPublicationProvider,
        external_id: str | None,
        external_url: str | None,
        published_at: datetime,
        idempotency_key: str,
    ) -> ContentItemDocument:
        """Persist an already-completed, explicit, approved publication action."""
        await self._require_active_profile(profile_id)
        current = await self.get_content(profile_id, content_id)
        if current.status is not ContentStatus.APPROVED:
            raise InvalidContentLifecycleTransitionError(
                "content must be approved before it can be published"
            )
        if current.revision != expected_revision:
            raise ConcurrencyConflictError("content item changed after it was read")
        payload = current.model_dump(by_alias=True, mode="python")
        payload.update(
            {
                "status": ContentStatus.PUBLISHED,
                "published_at": published_at,
                "external_url": external_url,
                "publication_provider": provider,
                "external_id": external_id,
                "publication_idempotency_key": idempotency_key,
            }
        )
        updated = await self._embedded_item(
            ContentItemDocument.model_validate(payload, context={"from_storage": True})
        )
        return await self._items(profile_id).update(updated, expected_revision=expected_revision)

    async def create_version(
        self,
        profile_id: str,
        content_id: str,
        *,
        expected_revision: int,
        body: str,
        change_reason: str,
        created_by: str | None,
    ) -> ContentItemDocument:
        """Record a new draft revision, including a published-item revision workflow."""
        await self._require_active_profile(profile_id)
        current = await self.get_content(profile_id, content_id)
        if current.revision != expected_revision:
            raise ContentVersionConflictError("content item changed after it was read")
        if current.status is ContentStatus.ARCHIVED:
            raise InvalidContentLifecycleTransitionError("archived content cannot be revised")
        return await self._append_version(
            current,
            body=body,
            title=current.title,
            change_reason=change_reason,
            created_by=created_by,
        )

    async def rollback_content(
        self,
        profile_id: str,
        content_id: str,
        *,
        expected_revision: int,
        rollback_to_version: int,
        change_reason: str,
        created_by: str | None,
    ) -> ContentItemDocument:
        """Make a historical snapshot the body of a new immutable revision."""
        await self._require_active_profile(profile_id)
        current = await self.get_content(profile_id, content_id)
        if current.revision != expected_revision:
            raise ContentVersionConflictError("content item changed after it was read")
        if current.status is ContentStatus.ARCHIVED:
            raise InvalidContentLifecycleTransitionError("archived content cannot be revised")
        source = await self._versions(profile_id).get(content_id, rollback_to_version)
        if source is None:
            raise ContentVersionNotFoundError("content version was not found")
        return await self._append_version(
            current,
            body=source.body,
            title=source.title,
            change_reason=change_reason,
            created_by=created_by,
        )

    async def _append_version(
        self,
        current: ContentItemDocument,
        *,
        body: str,
        title: str,
        change_reason: str,
        created_by: str | None,
    ) -> ContentItemDocument:
        """Build a draft snapshot without modifying a historical record."""
        next_version = current.current_version + 1
        payload = current.model_dump(by_alias=True, mode="python")
        payload.update(
            {
                "body": body,
                "title": title,
                "status": ContentStatus.DRAFTING,
                "current_version": next_version,
                # A revised draft has not itself been published.  The prior
                # published snapshot remains immutable in content_versions.
                "published_at": None,
                "external_url": None,
                "publication_provider": None,
                "external_id": None,
                "publication_idempotency_key": None,
            }
        )
        updated = ContentItemDocument.model_validate(payload, context={"from_storage": True})
        updated = await self._embedded_item(updated)
        version = ContentVersionDocument(
            profile_id=current.profile_id,
            content_id=current.content_id,
            version=next_version,
            title=title,
            body=body,
            status=ContentStatus.DRAFTING,
            change_reason=change_reason,
            created_by=created_by,
            tags=list(current.tags),
        )
        try:
            return await self._items(current.profile_id).append_version(
                updated,
                version,
                expected_revision=current.revision,
            )
        except ConcurrencyConflictError as error:
            raise ContentVersionConflictError(
                "content item changed during version creation"
            ) from error

    async def _embedded_idea(self, document: ContentIdeaDocument) -> ContentIdeaDocument:
        return await self._embedded(document, idea_embedding_text(document), ContentIdeaDocument)

    async def _embedded_brief(self, document: ContentBriefDocument) -> ContentBriefDocument:
        return await self._embedded(document, brief_embedding_text(document), ContentBriefDocument)

    async def _embedded_item(self, document: ContentItemDocument) -> ContentItemDocument:
        if document.status not in _EMBEDDABLE_ITEM_STATUSES:
            return self._without_embedding(document)
        return await self._embedded(document, item_embedding_text(document), ContentItemDocument)

    async def _embedded[
        TDocument: ContentIdeaDocument | ContentBriefDocument | ContentItemDocument
    ](
        self,
        document: TDocument,
        text: str,
        model_type: type[TDocument],
    ) -> TDocument:
        """Attach one vector while retaining model metadata for later re-indexing."""
        if self._embedder is None:
            return document
        vectors = await self._embedder.embed_chunks([text])
        if len(vectors) != 1:
            raise EmbeddingCompatibilityError("the embedder returned more than one vector")
        vector = vectors[0]
        payload = document.model_dump(by_alias=True, mode="python")
        payload.update(
            embedding=list(vector.values),
            embedding_model=vector.model,
            embedding_dimensions=vector.dimensions,
            embedding_generated_at=utc_now(),
        )
        return cast(TDocument, model_type.model_validate(payload, context={"from_storage": True}))

    @staticmethod
    def _without_embedding(
        document: ContentItemDocument,
    ) -> ContentItemDocument:
        """Remove an obsolete vector when a current item no longer represents a draft/post."""
        payload = document.model_dump(by_alias=True, mode="python")
        payload.update(
            embedding=None,
            embedding_model=None,
            embedding_dimensions=None,
            embedding_generated_at=None,
        )
        return ContentItemDocument.model_validate(payload, context={"from_storage": True})

    async def list_versions(
        self,
        profile_id: str,
        content_id: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ContentVersionDocument]:
        """Page immutable history after proving the item belongs to the profile."""
        await self.get_content(profile_id, content_id)
        return await self._versions(profile_id).list(
            content_id=content_id,
            limit=limit,
            cursor=cursor,
        )

    async def create_link(
        self,
        *,
        profile_id: str,
        link_id: str,
        content_id: str,
        source_type: str,
        source_id: str,
        project_id: str | None,
        relation: ContentRelationType,
        tags: Sequence[str],
    ) -> ContentLinkDocument:
        """Store a durable, manually supplied provenance relation."""
        await self._require_active_profile(profile_id)
        await self.get_content(profile_id, content_id)
        document = ContentLinkDocument(
            profile_id=profile_id,
            link_id=link_id,
            content_id=content_id,
            source_type=source_type,
            source_id=source_id,
            project_id=project_id,
            relation=relation,
            tags=list(tags),
        )
        try:
            return await self._links(profile_id).create(document)
        except DuplicateDocumentError as error:
            raise ContentLinkAlreadyExistsError("content link already exists") from error

    async def create_campaign(
        self,
        *,
        profile_id: str,
        campaign_id: str,
        name: str,
        description: str,
        content_ids: Sequence[str],
        platforms: Sequence[ContentPlatform],
        tags: Sequence[str],
        status: ContentCampaignStatus,
    ) -> ContentCampaignDocument:
        """Store campaign metadata after verifying every named item is owned here."""
        await self._require_active_profile(profile_id)
        for content_id in content_ids:
            await self.get_content(profile_id, content_id)
        document = ContentCampaignDocument(
            profile_id=profile_id,
            campaign_id=campaign_id,
            name=name,
            description=description,
            content_ids=list(content_ids),
            platforms=list(platforms),
            tags=list(tags),
            status=status,
        )
        try:
            return await self._campaigns(profile_id).create(document)
        except DuplicateDocumentError as error:
            raise ContentCampaignAlreadyExistsError("content campaign already exists") from error


def _validate_status_change(current: ContentStatus, requested: object) -> None:
    """Validate a normal lifecycle step while allowing no-op metadata patches."""
    if requested is None:
        return
    if not isinstance(requested, str):
        raise InvalidContentLifecycleTransitionError("invalid content lifecycle status")
    try:
        target = ContentStatus(requested)
    except ValueError as error:
        raise InvalidContentLifecycleTransitionError("invalid content lifecycle status") from error
    if target is current:
        return
    if target not in _ALLOWED_CONTENT_TRANSITIONS[current]:
        raise InvalidContentLifecycleTransitionError(
            f"content cannot transition from {current.value} to {target.value}"
        )
