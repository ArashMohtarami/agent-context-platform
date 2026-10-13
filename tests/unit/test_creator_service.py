"""V2 Phase 1 creator-profile service rules."""

import pytest

from app.creator.errors import ContentPillarAlreadyExistsError, CreatorProfileAlreadyExistsError
from app.models.common import Page
from app.models.content import ContentPillarDocument, CreatorProfileDocument
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError
from app.services.creator_service import CreatorService

PROFILE = "personal-brand"
OTHER_PROFILE = "team-brand"


class Profiles:
    """Small in-memory profile repository for service-rule tests."""

    def __init__(self) -> None:
        self.records: dict[str, CreatorProfileDocument] = {}

    async def create(self, document: CreatorProfileDocument) -> CreatorProfileDocument:
        if document.profile_id in self.records:
            raise DuplicateDocumentError("duplicate")
        self.records[document.profile_id] = document
        return document

    async def get(self, profile_id: str) -> CreatorProfileDocument | None:
        return self.records.get(profile_id)

    async def list(
        self,
        *,
        profile_ids: tuple[str, ...] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[CreatorProfileDocument]:
        del cursor
        records = tuple(
            record
            for profile_id, record in self.records.items()
            if profile_ids is None or profile_id in profile_ids
        )
        return Page(items=records[:limit])

    async def update(
        self, document: CreatorProfileDocument, *, expected_revision: int
    ) -> CreatorProfileDocument:
        current = self.records.get(document.profile_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("changed")
        stored = document.model_copy(update={"revision": expected_revision + 1})
        self.records[stored.profile_id] = stored
        return stored


class Pillars:
    """One profile-isolated in-memory pillar repository per profile."""

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        self.records: dict[str, ContentPillarDocument] = {}

    async def create(self, document: ContentPillarDocument) -> ContentPillarDocument:
        assert document.profile_id == self.profile_id
        if document.pillar_id in self.records:
            raise DuplicateDocumentError("duplicate")
        self.records[document.pillar_id] = document
        return document

    async def get(self, pillar_id: str) -> ContentPillarDocument | None:
        return self.records.get(pillar_id)

    async def list(
        self, *, limit: int = 20, cursor: str | None = None
    ) -> Page[ContentPillarDocument]:
        del cursor
        return Page(items=tuple(self.records.values())[:limit])

    async def update(
        self, document: ContentPillarDocument, *, expected_revision: int
    ) -> ContentPillarDocument:
        current = self.records.get(document.pillar_id)
        if current is None or current.revision != expected_revision:
            raise ConcurrencyConflictError("changed")
        stored = document.model_copy(update={"revision": expected_revision + 1})
        self.records[stored.pillar_id] = stored
        return stored


def service() -> CreatorService:
    profiles = Profiles()
    pillars: dict[str, Pillars] = {}
    return CreatorService(
        profiles, lambda profile_id: pillars.setdefault(profile_id, Pillars(profile_id))
    )


async def create_profile(
    creator: CreatorService, profile_id: str = PROFILE
) -> CreatorProfileDocument:
    return await creator.create_profile(
        profile_id=profile_id,
        name="Technical Personal Brand",
        status="active",
        audiences=["backend engineers"],
        domains=["payments"],
        voice={"tone": ["direct"], "avoid": ["hype"]},
        writing_preferences=["short paragraphs"],
        formatting_preferences=[],
        terminology_preferences=[],
        language_preferences=["English"],
        tone_preferences=["practical"],
        topics_of_interest=["idempotency"],
        topics_to_avoid=["fabricated stories"],
        content_objectives=["teach"],
        tags=["backend"],
    )


@pytest.mark.asyncio
async def test_profiles_are_revision_guarded_and_listed_only_from_the_allowed_grant() -> None:
    creator = service()
    profile = await create_profile(creator)
    await create_profile(creator, OTHER_PROFILE)

    updated = await creator.update_profile(
        PROFILE,
        expected_revision=profile.revision,
        changes={"status": "inactive", "tone_preferences": ["calm", "practical"]},
    )
    assert updated.status == "inactive"
    assert updated.revision == 2
    assert updated.tone_preferences == ["calm", "practical"]
    assert [
        item.profile_id
        for item in (await creator.list_profiles(allowed_profile_ids=(PROFILE,))).items
    ] == [PROFILE]
    with pytest.raises(ConcurrencyConflictError):
        await creator.update_profile(PROFILE, expected_revision=1, changes={"name": "Stale"})
    with pytest.raises(CreatorProfileAlreadyExistsError):
        await create_profile(creator)


@pytest.mark.asyncio
async def test_pillars_are_owned_by_one_existing_profile_and_are_revision_guarded() -> None:
    creator = service()
    await create_profile(creator)
    pillar = await creator.create_pillar(
        profile_id=PROFILE,
        pillar_id="distributed-systems",
        name="Distributed Systems",
        description="Practical distributed-systems lessons.",
        tags=["architecture"],
        priority=0.9,
        status="active",
    )

    assert (await creator.list_pillars(PROFILE)).items == (pillar,)
    updated = await creator.update_pillar(
        PROFILE,
        pillar.pillar_id,
        expected_revision=1,
        changes={"priority": 0.8},
    )
    assert updated.priority == 0.8
    assert updated.revision == 2
    with pytest.raises(ContentPillarAlreadyExistsError):
        await creator.create_pillar(
            profile_id=PROFILE,
            pillar_id=pillar.pillar_id,
            name=pillar.name,
            description=pillar.description,
            tags=[],
            priority=pillar.priority,
            status=pillar.status,
        )
    with pytest.raises(ConcurrencyConflictError):
        await creator.update_pillar(
            PROFILE,
            pillar.pillar_id,
            expected_revision=1,
            changes={"name": "Stale"},
        )
