"""Creator-profile and content-pillar business rules for V2 Phase 1."""

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from app.creator.errors import (
    ContentPillarAlreadyExistsError,
    ContentPillarNotFoundError,
    CreatorProfileAlreadyExistsError,
    CreatorProfileNotFoundError,
)
from app.models.common import Page
from app.models.content import (
    ContentPillarDocument,
    ContentPillarStatus,
    CreatorProfileDocument,
    CreatorProfileStatus,
    CreatorVoice,
)
from app.repositories.errors import ConcurrencyConflictError, DuplicateDocumentError


class CreatorProfileRepository(Protocol):
    """Persistence operations the creator-profile service needs."""

    async def create(self, document: CreatorProfileDocument) -> CreatorProfileDocument: ...

    async def get(self, profile_id: str) -> CreatorProfileDocument | None: ...

    async def list(
        self,
        *,
        profile_ids: tuple[str, ...] | None = None,
        limit: int = ...,
        cursor: str | None = ...,
    ) -> Page[CreatorProfileDocument]: ...

    async def update(
        self, document: CreatorProfileDocument, *, expected_revision: int
    ) -> CreatorProfileDocument: ...


class ContentPillarRepository(Protocol):
    """Profile-isolated pillar persistence required by this phase."""

    async def create(self, document: ContentPillarDocument) -> ContentPillarDocument: ...

    async def get(self, pillar_id: str) -> ContentPillarDocument | None: ...

    async def list(
        self, *, limit: int = ..., cursor: str | None = ...
    ) -> Page[ContentPillarDocument]: ...

    async def update(
        self, document: ContentPillarDocument, *, expected_revision: int
    ) -> ContentPillarDocument: ...


class CreatorService:
    """Own creator preference data without generating or interpreting prose."""

    def __init__(
        self,
        profiles: CreatorProfileRepository,
        pillars: Callable[[str], ContentPillarRepository],
    ) -> None:
        self._profiles = profiles
        self._pillars = pillars

    async def create_profile(
        self,
        *,
        profile_id: str,
        name: str,
        status: CreatorProfileStatus,
        audiences: Sequence[str],
        domains: Sequence[str],
        voice: CreatorVoice,
        writing_preferences: Sequence[str],
        formatting_preferences: Sequence[str],
        terminology_preferences: Sequence[str],
        language_preferences: Sequence[str],
        tone_preferences: Sequence[str],
        topics_of_interest: Sequence[str],
        topics_to_avoid: Sequence[str],
        content_objectives: Sequence[str],
        tags: Sequence[str],
    ) -> CreatorProfileDocument:
        """Create a deterministic profile, refusing duplicate public IDs."""
        document = CreatorProfileDocument(
            profile_id=profile_id,
            name=name,
            status=status,
            audiences=list(audiences),
            domains=list(domains),
            voice=voice,
            writing_preferences=list(writing_preferences),
            formatting_preferences=list(formatting_preferences),
            terminology_preferences=list(terminology_preferences),
            language_preferences=list(language_preferences),
            tone_preferences=list(tone_preferences),
            topics_of_interest=list(topics_of_interest),
            topics_to_avoid=list(topics_to_avoid),
            content_objectives=list(content_objectives),
            tags=list(tags),
        )
        try:
            return await self._profiles.create(document)
        except DuplicateDocumentError as error:
            raise CreatorProfileAlreadyExistsError("creator profile already exists") from error

    async def get_profile(self, profile_id: str) -> CreatorProfileDocument:
        """Load one profile after the API has checked its access grant."""
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise CreatorProfileNotFoundError("creator profile was not found")
        return profile

    async def list_profiles(
        self,
        *,
        allowed_profile_ids: tuple[str, ...] | None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[CreatorProfileDocument]:
        """List only profiles allowed by the caller's profile grant."""
        return await self._profiles.list(
            profile_ids=allowed_profile_ids,
            limit=limit,
            cursor=cursor,
        )

    async def update_profile(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        changes: Mapping[str, object],
    ) -> CreatorProfileDocument:
        """Apply a validated partial preference update with optimistic locking."""
        current = await self.get_profile(profile_id)
        if current.revision != expected_revision:
            raise ConcurrencyConflictError("creator profile changed after it was read")
        payload = current.model_dump(by_alias=True, mode="python")
        payload.update(changes)
        updated = CreatorProfileDocument.model_validate(payload)
        return await self._profiles.update(updated, expected_revision=expected_revision)

    async def create_pillar(
        self,
        *,
        profile_id: str,
        pillar_id: str,
        name: str,
        description: str,
        tags: Sequence[str],
        priority: float,
        status: ContentPillarStatus,
    ) -> ContentPillarDocument:
        """Create a profile-owned theme only for an existing profile."""
        await self.get_profile(profile_id)
        document = ContentPillarDocument(
            profile_id=profile_id,
            pillar_id=pillar_id,
            name=name,
            description=description,
            tags=list(tags),
            priority=priority,
            status=status,
        )
        try:
            return await self._pillars(profile_id).create(document)
        except DuplicateDocumentError as error:
            raise ContentPillarAlreadyExistsError("content pillar already exists") from error

    async def list_pillars(
        self,
        profile_id: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[ContentPillarDocument]:
        """List pillars inside one existing creator-profile boundary."""
        await self.get_profile(profile_id)
        return await self._pillars(profile_id).list(limit=limit, cursor=cursor)

    async def update_pillar(
        self,
        profile_id: str,
        pillar_id: str,
        *,
        expected_revision: int,
        changes: Mapping[str, object],
    ) -> ContentPillarDocument:
        """Update one pillar inside its profile, never across profiles."""
        await self.get_profile(profile_id)
        repository = self._pillars(profile_id)
        current = await repository.get(pillar_id)
        if current is None:
            raise ContentPillarNotFoundError("content pillar was not found")
        if current.revision != expected_revision:
            raise ConcurrencyConflictError("content pillar changed after it was read")
        payload = current.model_dump(by_alias=True, mode="python")
        payload.update(changes)
        updated = ContentPillarDocument.model_validate(payload)
        return await repository.update(updated, expected_revision=expected_revision)
