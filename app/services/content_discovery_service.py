"""Deterministic V2 idea discovery and agent-facing brief preparation.

This service deliberately assembles evidence; it does not call an LLM or turn
evidence into publishable prose.  The returned labels and constraints are
structured data for an agent to inspect, revise, and explicitly save.
"""

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from bson import ObjectId

from app.content.discovery import DiscoveryTimeRange
from app.content.errors import ContentIdeaNotFoundError
from app.creator.errors import ContentPillarNotFoundError, CreatorProfileNotFoundError
from app.models.common import Page, utc_now
from app.models.content import (
    ContentIdeaDocument,
    ContentItemDocument,
    ContentPillarDocument,
    ContentPlatform,
    ContentSourceReference,
    ContentStatus,
    CreatorProfileDocument,
)
from app.models.memory import MemoryDocument
from app.models.project import ProjectDocument
from app.models.project_context import ProjectContextDocument
from app.models.skill import SkillDocument
from app.projects.errors import ProjectNotFoundError

DiscoverySourceType = Literal["project_context", "memory", "skill", "content_item"]
_MAX_SOURCE_SCAN = 50
_MAX_PREVIOUS_CONTENT = 3
_CONTENT_SKILL_IDS = frozenset({"linkedin-writing", "medium-writing", "github-writing"})
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]*", re.IGNORECASE)


class CreatorProfileLookup(Protocol):
    """Read the profile that owns all Phase 5 material."""

    async def get(self, profile_id: str) -> CreatorProfileDocument | None: ...


class ProjectLookup(Protocol):
    """Read an explicitly requested V1 project before its data is considered."""

    async def get(self, project_id: str) -> ProjectDocument | None: ...


class PillarRepository(Protocol):
    """Profile-bound pillar reads used to constrain topics."""

    async def get(self, pillar_id: str) -> ContentPillarDocument | None: ...

    async def list(
        self, *, limit: int = ..., cursor: str | None = ...
    ) -> Page[ContentPillarDocument]: ...


class IdeaRepository(Protocol):
    """Profile-bound current-idea reads used for exact duplicate avoidance."""

    async def get(self, idea_id: str) -> ContentIdeaDocument | None: ...

    async def list_recent(
        self,
        *,
        updated_after: datetime | None = ...,
        updated_before: datetime | None = ...,
        limit: int = ...,
    ) -> tuple[ContentIdeaDocument, ...]: ...


class ContentItemRepository(Protocol):
    """Profile-bound current content reads used only for content history."""

    async def get(self, content_id: str) -> ContentItemDocument | None: ...

    async def list_recent(
        self,
        *,
        statuses: Sequence[ContentStatus] = ...,
        updated_after: datetime | None = ...,
        updated_before: datetime | None = ...,
        limit: int = ...,
    ) -> tuple[ContentItemDocument, ...]: ...


class ContextRepository(Protocol):
    """Project-bound V1 context reads; no cross-project list exists here."""

    async def get(self, document_id: ObjectId) -> ProjectContextDocument | None: ...

    async def list_recent(
        self,
        *,
        updated_after: datetime | None = ...,
        updated_before: datetime | None = ...,
        limit: int = ...,
    ) -> tuple[ProjectContextDocument, ...]: ...


class MemoryRepository(Protocol):
    """One immutable V1 memory scope's recent reads."""

    async def get(self, document_id: ObjectId) -> MemoryDocument | None: ...

    async def list_recent(
        self,
        *,
        updated_after: datetime | None = ...,
        updated_before: datetime | None = ...,
        limit: int = ...,
    ) -> tuple[MemoryDocument, ...]: ...


class SkillRepository(Protocol):
    """Trusted active skill metadata used as factual supporting material."""

    async def get_active(self, skill_id: str) -> SkillDocument | None: ...

    async def list_recent(
        self,
        *,
        updated_after: datetime | None = ...,
        updated_before: datetime | None = ...,
        limit: int = ...,
    ) -> tuple[SkillDocument, ...]: ...


@dataclass(frozen=True, slots=True)
class DiscoveryFreshness:
    """The deterministic recency metadata behind a candidate."""

    updated_at: datetime
    age_days: int
    inside_requested_range: bool


@dataclass(frozen=True, slots=True)
class DiscoveryEvidence:
    """A compact, attributable V1/V2 record an agent may reason from."""

    source: ContentSourceReference
    source_type: DiscoverySourceType
    title: str
    excerpt: str
    tags: tuple[str, ...]
    freshness: DiscoveryFreshness


@dataclass(frozen=True, slots=True)
class SimilarPublishedContent:
    """A published item that shares explicit terms, tags, pillar, or project."""

    content_id: str
    title: str
    platform: ContentPlatform
    published_at: datetime | None
    reason: str


@dataclass(frozen=True, slots=True)
class ExistingIdea:
    """An unpublished profile-owned idea retained to make duplicate avoidance visible."""

    idea_id: str
    title: str
    pillar_id: str | None
    platforms: tuple[ContentPlatform, ...]
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ContentIdeaCandidate:
    """One evidence-backed topic label, deliberately not generated prose."""

    topic: str
    source_refs: tuple[ContentSourceReference, ...]
    evidence: tuple[DiscoveryEvidence, ...]
    related_project_id: str | None
    related_pillar_id: str | None
    suggested_platforms: tuple[ContentPlatform, ...]
    why_useful: tuple[str, ...]
    previous_similar_content: tuple[SimilarPublishedContent, ...]
    freshness: DiscoveryFreshness


@dataclass(frozen=True, slots=True)
class ContentIdeaDiscovery:
    """A bounded structured input package for an agent; nothing is saved."""

    profile_id: str
    project_id: str | None
    pillar_id: str | None
    platform: ContentPlatform | None
    candidates: tuple[ContentIdeaCandidate, ...]
    existing_unpublished_ideas: tuple[ExistingIdea, ...]


@dataclass(frozen=True, slots=True)
class SkillReference:
    """A trusted active skill selected as an input, not executable instruction."""

    skill_id: str
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class ContentBriefPreparation:
    """A non-persistent evidence plan an agent may refine before creating a brief."""

    profile_id: str
    idea_id: str
    platform: ContentPlatform
    objective: str
    audience: tuple[str, ...]
    primary_message: str
    supporting_points: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]
    technical_skills: tuple[SkillReference, ...]
    content_skill: SkillReference | None
    similar_previous_content: tuple[SimilarPublishedContent, ...]
    content_constraints: tuple[str, ...]


class ContentDiscoveryService:
    """Assemble Phase 5 inputs without generation, publication, or direct DB access."""

    def __init__(
        self,
        profiles: CreatorProfileLookup,
        projects: ProjectLookup,
        pillars: Callable[[str], PillarRepository],
        ideas: Callable[[str], IdeaRepository],
        items: Callable[[str], ContentItemRepository],
        contexts: Callable[[str], ContextRepository],
        memories: Callable[[str | None], MemoryRepository],
        skills: SkillRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._profiles = profiles
        self._projects = projects
        self._pillars = pillars
        self._ideas = ideas
        self._items = items
        self._contexts = contexts
        self._memories = memories
        self._skills = skills
        self._clock = clock

    async def discover_content_ideas(
        self,
        *,
        profile_id: str,
        project_id: str | None = None,
        pillar_id: str | None = None,
        time_range: DiscoveryTimeRange | None = None,
        platform: ContentPlatform | None = None,
        limit: int = 10,
    ) -> ContentIdeaDiscovery:
        """Return source-backed topic labels from the caller's permitted scope.

        Project context is never enumerated unless a caller explicitly names a
        project.  This makes the optional project filter an isolation boundary,
        not a broad search convenience.
        """
        _validate_limit(limit)
        profile = await self._profile(profile_id)
        if project_id is not None:
            await self._project(project_id)
        selected_pillar, pillars = await self._pillars_for(profile_id, pillar_id)
        after = None if time_range is None else time_range.start_at
        before = None if time_range is None else time_range.end_at
        context_task = (
            self._contexts(project_id).list_recent(
                updated_after=after, updated_before=before, limit=_MAX_SOURCE_SCAN
            )
            if project_id is not None
            else _empty_context()
        )
        project_memory_task = (
            self._memories(project_id).list_recent(
                updated_after=after, updated_before=before, limit=_MAX_SOURCE_SCAN
            )
            if project_id is not None
            else _empty_memory()
        )
        (
            contexts,
            project_memories,
            global_memories,
            skills,
            published,
            existing_ideas,
        ) = await asyncio.gather(
            context_task,
            project_memory_task,
            self._memories(None).list_recent(
                updated_after=after, updated_before=before, limit=_MAX_SOURCE_SCAN
            ),
            self._skills.list_recent(
                updated_after=after, updated_before=before, limit=_MAX_SOURCE_SCAN
            ),
            self._items(profile_id).list_recent(
                statuses=(ContentStatus.PUBLISHED,),
                updated_after=after,
                updated_before=before,
                limit=_MAX_SOURCE_SCAN,
            ),
            # Exact duplicate suppression considers all current unpublished
            # ideas, not merely ones inside the recent-work time range.
            self._ideas(profile_id).list_recent(limit=_MAX_SOURCE_SCAN),
        )
        safe_published = tuple(
            item for item in published if _content_is_safe(item, requested_project_id=project_id)
        )
        unpublished = tuple(
            idea
            for idea in existing_ideas
            if idea.status not in {ContentStatus.PUBLISHED, ContentStatus.ARCHIVED}
            and _idea_is_safe(idea, requested_project_id=project_id)
        )
        duplicate_topics = {_normal_topic(idea.title) for idea in unpublished}
        evidence = [
            *(_context_evidence(document, time_range, self._clock()) for document in contexts),
            *(
                _memory_evidence(document, time_range, self._clock())
                for document in (*project_memories, *global_memories)
            ),
            *(
                _skill_evidence(document, time_range, self._clock())
                for document in skills
                if document.trust_level == "trusted"
            ),
        ]
        candidates = self._candidates(
            evidence=evidence,
            profile=profile,
            pillars=pillars,
            selected_pillar=selected_pillar,
            requested_project_id=project_id,
            platform=platform,
            duplicate_topics=duplicate_topics,
            published=safe_published,
            limit=limit,
        )
        return ContentIdeaDiscovery(
            profile_id=profile_id,
            project_id=project_id,
            pillar_id=None if selected_pillar is None else selected_pillar.pillar_id,
            platform=platform,
            candidates=candidates,
            existing_unpublished_ideas=tuple(_existing_idea(idea) for idea in unpublished[:limit]),
        )

    async def prepare_content_brief(
        self,
        *,
        profile_id: str,
        idea_id: str,
        platform: ContentPlatform,
        project_id: str | None = None,
        pillar_id: str | None = None,
    ) -> ContentBriefPreparation:
        """Construct a reviewable brief input from one saved idea and retained evidence."""
        profile = await self._profile(profile_id)
        idea = await self._idea(profile_id, idea_id)
        if project_id is not None and idea.origin.project_id not in {None, project_id}:
            raise ValueError("the requested project does not match the idea provenance")
        effective_project_id = idea.origin.project_id if project_id is None else project_id
        if effective_project_id is not None:
            await self._project(effective_project_id)
        selected_pillar, _ = await self._pillars_for(profile_id, pillar_id or idea.pillar_id)
        source_refs = tuple(
            ContentSourceReference(
                source_type=idea.origin.type,
                source_id=source_id,
                project_id=idea.origin.project_id,
            )
            for source_id in idea.origin.source_ids
        )
        evidence = await self._origin_evidence(source_refs, profile_id)
        recent_skills, published = await asyncio.gather(
            self._skills.list_recent(limit=_MAX_SOURCE_SCAN),
            self._items(profile_id).list_recent(
                statuses=(ContentStatus.PUBLISHED,), limit=_MAX_SOURCE_SCAN
            ),
        )
        safe_published = tuple(
            item
            for item in published
            if _content_is_safe(item, requested_project_id=effective_project_id)
        )
        technical_skills = _technical_skills(
            recent_skills,
            title=idea.title,
            description=idea.description,
            pillar=selected_pillar,
            source_refs=source_refs,
        )
        content_skill = _content_skill(recent_skills, platform)
        supporting_points = tuple(dict.fromkeys(item.title for item in evidence if item.title))
        if not supporting_points:
            supporting_points = (idea.title,)
        return ContentBriefPreparation(
            profile_id=profile_id,
            idea_id=idea_id,
            platform=platform,
            objective=f"Prepare a {_platform_value(platform)} piece about {idea.title}",
            audience=tuple(profile.audiences),
            primary_message=idea.description,
            supporting_points=supporting_points,
            source_refs=source_refs,
            technical_skills=technical_skills,
            content_skill=content_skill,
            similar_previous_content=_similar_published(
                title=idea.title,
                tags=tuple(idea.tags),
                pillar_id=idea.pillar_id,
                project_id=effective_project_id,
                platform=platform,
                published=safe_published,
            ),
            content_constraints=_constraints(profile, platform, selected_pillar),
        )

    async def _profile(self, profile_id: str) -> CreatorProfileDocument:
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise CreatorProfileNotFoundError("creator profile was not found")
        return profile

    async def _project(self, project_id: str) -> ProjectDocument:
        project = await self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError("project was not found")
        return project

    async def _idea(self, profile_id: str, idea_id: str) -> ContentIdeaDocument:
        idea = await self._ideas(profile_id).get(idea_id)
        if idea is None:
            raise ContentIdeaNotFoundError("content idea was not found")
        return idea

    async def _pillars_for(
        self, profile_id: str, requested_pillar_id: str | None
    ) -> tuple[ContentPillarDocument | None, tuple[ContentPillarDocument, ...]]:
        repository = self._pillars(profile_id)
        if requested_pillar_id is not None:
            selected = await repository.get(requested_pillar_id)
            if selected is None:
                raise ContentPillarNotFoundError("content pillar was not found")
            return selected, (selected,)
        page = await repository.list(limit=_MAX_SOURCE_SCAN)
        return None, tuple(pillar for pillar in page.items if pillar.status == "active")

    def _candidates(
        self,
        *,
        evidence: Sequence[DiscoveryEvidence],
        profile: CreatorProfileDocument,
        pillars: Sequence[ContentPillarDocument],
        selected_pillar: ContentPillarDocument | None,
        requested_project_id: str | None,
        platform: ContentPlatform | None,
        duplicate_topics: set[str],
        published: Sequence[ContentItemDocument],
        limit: int,
    ) -> tuple[ContentIdeaCandidate, ...]:
        # A saved idea has one origin type and one optional project boundary.
        # Keep both separate so a caller can persist every displayed source ID
        # without silently discarding a source from a differently shaped origin.
        grouped: dict[tuple[str, str, str | None], list[DiscoveryEvidence]] = {}
        for item in evidence:
            if (
                not item.freshness.inside_requested_range
                or _is_avoided(item, profile)
                or not _is_relevant(item, profile, pillars, selected_pillar)
            ):
                continue
            topic = _normal_topic(item.title)
            if topic and topic not in duplicate_topics:
                key = (topic, item.source_type, item.source.project_id)
                grouped.setdefault(key, []).append(item)
        candidates: list[ContentIdeaCandidate] = []
        for entries in grouped.values():
            newest = max(entries, key=lambda item: item.freshness.updated_at)
            related_pillar = selected_pillar or _matching_pillar(newest, pillars)
            refs = _unique_source_refs(entries)
            reason_values = [
                *(f"recent {item.source_type.replace('_', ' ')}" for item in entries),
            ]
            if related_pillar is not None:
                reason_values.append(f"matches pillar {related_pillar.name}")
            if _matches_profile(newest, profile):
                reason_values.append("matches creator profile interests")
            reasons = tuple(dict.fromkeys(reason_values))
            candidates.append(
                ContentIdeaCandidate(
                    topic=newest.title,
                    source_refs=refs,
                    evidence=tuple(entries),
                    related_project_id=_related_project(entries, requested_project_id),
                    related_pillar_id=None if related_pillar is None else related_pillar.pillar_id,
                    suggested_platforms=(platform,)
                    if platform is not None
                    else tuple(ContentPlatform),
                    why_useful=reasons,
                    previous_similar_content=_similar_published(
                        title=newest.title,
                        tags=newest.tags,
                        pillar_id=None if related_pillar is None else related_pillar.pillar_id,
                        project_id=requested_project_id,
                        platform=platform,
                        published=published,
                    ),
                    freshness=newest.freshness,
                )
            )
        candidates.sort(key=lambda item: item.freshness.updated_at, reverse=True)
        return tuple(candidates[:limit])

    async def _origin_evidence(
        self,
        source_refs: Sequence[ContentSourceReference],
        profile_id: str,
    ) -> tuple[DiscoveryEvidence, ...]:
        evidence: list[DiscoveryEvidence] = []
        for source in source_refs:
            document = await self._source_document(source, profile_id)
            if document is not None:
                evidence.append(document)
        return tuple(evidence)

    async def _source_document(
        self, source: ContentSourceReference, profile_id: str
    ) -> DiscoveryEvidence | None:
        now = self._clock()
        if source.source_type == "project_context" and source.project_id is not None:
            document_id = _object_id(source.source_id)
            if document_id is None:
                return None
            context = await self._contexts(source.project_id).get(document_id)
            return None if context is None else _context_evidence(context, None, now)
        if source.source_type == "memory":
            document_id = _object_id(source.source_id)
            if document_id is None:
                return None
            memory = await self._memories(source.project_id).get(document_id)
            return None if memory is None else _memory_evidence(memory, None, now)
        if source.source_type == "skill":
            skill = await self._skills.get_active(source.source_id)
            if skill is None or skill.trust_level != "trusted":
                return None
            return _skill_evidence(skill, None, now)
        if source.source_type == "content_item":
            item = await self._items(profile_id).get(source.source_id)
            if item is None:
                return None
            return _item_evidence(item, None, now)
        return None


async def _empty_context() -> tuple[ProjectContextDocument, ...]:
    return ()


async def _empty_memory() -> tuple[MemoryDocument, ...]:
    return ()


def _context_evidence(
    document: ProjectContextDocument,
    time_range: DiscoveryTimeRange | None,
    now: datetime,
) -> DiscoveryEvidence:
    return _evidence(
        source_type="project_context",
        source_id=str(document.id),
        project_id=document.project_id,
        title=document.title,
        content=document.content,
        tags=tuple(document.tags),
        updated_at=document.updated_at,
        time_range=time_range,
        now=now,
    )


def _memory_evidence(
    document: MemoryDocument,
    time_range: DiscoveryTimeRange | None,
    now: datetime,
) -> DiscoveryEvidence:
    return _evidence(
        source_type="memory",
        source_id=str(document.id),
        project_id=document.project_id,
        title=document.summary or _first_line(document.content),
        content=document.content,
        tags=tuple(document.tags),
        updated_at=document.updated_at,
        time_range=time_range,
        now=now,
    )


def _skill_evidence(
    document: SkillDocument,
    time_range: DiscoveryTimeRange | None,
    now: datetime,
) -> DiscoveryEvidence:
    return _evidence(
        source_type="skill",
        source_id=document.skill_id,
        project_id=None,
        title=document.name,
        content=document.description,
        tags=tuple(document.tags),
        updated_at=document.updated_at,
        time_range=time_range,
        now=now,
    )


def _item_evidence(
    document: ContentItemDocument,
    time_range: DiscoveryTimeRange | None,
    now: datetime,
) -> DiscoveryEvidence:
    return _evidence(
        source_type="content_item",
        source_id=document.content_id,
        project_id=None,
        title=document.title,
        content=document.body,
        tags=tuple((*document.topic_tags, *document.tags)),
        updated_at=document.updated_at,
        time_range=time_range,
        now=now,
    )


def _evidence(
    *,
    source_type: DiscoverySourceType,
    source_id: str,
    project_id: str | None,
    title: str,
    content: str,
    tags: tuple[str, ...],
    updated_at: datetime,
    time_range: DiscoveryTimeRange | None,
    now: datetime,
) -> DiscoveryEvidence:
    age_days = max(0, int((now - updated_at).total_seconds() // 86_400))
    return DiscoveryEvidence(
        source=ContentSourceReference(
            source_type=source_type,
            source_id=source_id,
            project_id=project_id,
        ),
        source_type=source_type,
        title=title,
        excerpt=_excerpt(content),
        tags=tags,
        freshness=DiscoveryFreshness(
            updated_at=updated_at,
            age_days=age_days,
            inside_requested_range=time_range is None or time_range.includes(updated_at),
        ),
    )


def _existing_idea(document: ContentIdeaDocument) -> ExistingIdea:
    return ExistingIdea(
        idea_id=document.idea_id,
        title=document.title,
        pillar_id=document.pillar_id,
        platforms=tuple(document.platforms),
        updated_at=document.updated_at,
    )


def _content_is_safe(item: ContentItemDocument, *, requested_project_id: str | None) -> bool:
    projects = set(item.project_ids)
    projects.update(ref.project_id for ref in item.source_refs if ref.project_id is not None)
    return (
        not projects if requested_project_id is None else projects.issubset({requested_project_id})
    )


def _idea_is_safe(idea: ContentIdeaDocument, *, requested_project_id: str | None) -> bool:
    return idea.origin.project_id is None or idea.origin.project_id == requested_project_id


def _is_relevant(
    evidence: DiscoveryEvidence,
    profile: CreatorProfileDocument,
    pillars: Sequence[ContentPillarDocument],
    selected_pillar: ContentPillarDocument | None,
) -> bool:
    """Keep a source in scope only when stored creator data makes it relevant.

    A named project is an authorization boundary, not permission to turn every
    project record into a content suggestion.  A selected pillar is exact; in
    its absence, any active pillar or the profile's stored interests may make
    an evidence record eligible.  With no configured relevance data there is
    intentionally no guess and therefore no candidate.
    """
    if selected_pillar is not None:
        return _matches_pillar(evidence, selected_pillar)
    return _matching_pillar(evidence, pillars) is not None or _matches_profile(evidence, profile)


def _matching_pillar(
    evidence: DiscoveryEvidence, pillars: Sequence[ContentPillarDocument]
) -> ContentPillarDocument | None:
    return next((pillar for pillar in pillars if _matches_pillar(evidence, pillar)), None)


def _matches_pillar(evidence: DiscoveryEvidence, pillar: ContentPillarDocument) -> bool:
    target = _terms(pillar.pillar_id, pillar.name, *pillar.tags)
    observed = _terms(evidence.title, evidence.excerpt, *evidence.tags)
    return bool(target & observed)


def _matches_profile(evidence: DiscoveryEvidence, profile: CreatorProfileDocument) -> bool:
    desired = _terms(*profile.domains, *profile.topics_of_interest, *profile.content_objectives)
    observed = _terms(evidence.title, evidence.excerpt, *evidence.tags)
    return bool(desired & observed)


def _is_avoided(evidence: DiscoveryEvidence, profile: CreatorProfileDocument) -> bool:
    observed = _normal_topic(f"{evidence.title} {evidence.excerpt}")
    return any(_normal_topic(topic) in observed for topic in profile.topics_to_avoid if topic)


def _related_project(
    evidence: Sequence[DiscoveryEvidence], requested_project_id: str | None
) -> str | None:
    if requested_project_id is not None:
        return requested_project_id
    return next((item.source.project_id for item in evidence if item.source.project_id), None)


def _similar_published(
    *,
    title: str,
    tags: Sequence[str],
    pillar_id: str | None,
    project_id: str | None,
    platform: ContentPlatform | None,
    published: Sequence[ContentItemDocument],
) -> tuple[SimilarPublishedContent, ...]:
    target_terms = _terms(title, *tags, pillar_id or "")
    matches: list[SimilarPublishedContent] = []
    for item in published:
        if platform is not None and _platform_value(item.platform) != _platform_value(platform):
            continue
        reasons: list[str] = []
        if pillar_id is not None and item.pillar_id == pillar_id:
            reasons.append("same pillar")
        if project_id is not None and project_id in item.project_ids:
            reasons.append("same project")
        if target_terms & _terms(item.title, *item.topic_tags, *item.tags):
            reasons.append("shared explicit terms")
        if reasons:
            matches.append(
                SimilarPublishedContent(
                    content_id=item.content_id,
                    title=item.title,
                    platform=item.platform,
                    published_at=item.published_at,
                    reason=", ".join(reasons),
                )
            )
    return tuple(matches[:_MAX_PREVIOUS_CONTENT])


def _technical_skills(
    skills: Sequence[SkillDocument],
    *,
    title: str,
    description: str,
    pillar: ContentPillarDocument | None,
    source_refs: Sequence[ContentSourceReference],
) -> tuple[SkillReference, ...]:
    explicit = {ref.source_id for ref in source_refs if ref.source_type == "skill"}
    target = _terms(title, description, *(pillar.tags if pillar is not None else ()))
    matched = [
        skill
        for skill in skills
        if skill.trust_level == "trusted"
        and skill.skill_id not in _CONTENT_SKILL_IDS
        and (
            skill.skill_id in explicit
            or bool(target & _terms(skill.name, skill.description, *skill.tags))
        )
    ]
    return tuple(
        SkillReference(skill.skill_id, skill.name, skill.version)
        for skill in matched[:_MAX_PREVIOUS_CONTENT]
    )


def _content_skill(
    skills: Sequence[SkillDocument], platform: ContentPlatform
) -> SkillReference | None:
    desired = f"{_platform_value(platform)}-writing"
    skill = next(
        (item for item in skills if item.skill_id == desired and item.trust_level == "trusted"),
        None,
    )
    return None if skill is None else SkillReference(skill.skill_id, skill.name, skill.version)


def _constraints(
    profile: CreatorProfileDocument,
    platform: ContentPlatform,
    pillar: ContentPillarDocument | None,
) -> tuple[str, ...]:
    """Return stored preferences and fixed provenance rules as data labels."""
    values = [
        f"platform: {_platform_value(platform)}",
        *(f"voice: {item}" for item in profile.voice.tone),
        *(f"avoid: {item}" for item in profile.voice.avoid),
        *(f"writing preference: {item}" for item in profile.writing_preferences),
        *(f"formatting preference: {item}" for item in profile.formatting_preferences),
        *(f"topic to avoid: {item}" for item in profile.topics_to_avoid),
        *(f"pillar: {pillar.name}" for _ in [0] if pillar is not None),
        "ground claims in the listed source references",
        "do not fabricate project experiences",
    ]
    return tuple(dict.fromkeys(values))


def _object_id(value: str) -> ObjectId | None:
    return ObjectId(value) if ObjectId.is_valid(value) else None


def _excerpt(value: str, maximum: int = 600) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= maximum else f"{normalized[: maximum - 1]}…"


def _first_line(value: str) -> str:
    return next((line.strip() for line in value.splitlines() if line.strip()), value)


def _terms(*values: str) -> set[str]:
    return {match.group(0).casefold() for value in values for match in _TOKEN.finditer(value)}


def _normal_topic(value: str) -> str:
    return " ".join(match.group(0).casefold() for match in _TOKEN.finditer(value))


def _platform_value(value: ContentPlatform | str) -> str:
    """Support validated StrEnums and Pydantic's string-backed representation."""
    return str(value)


def _unique_source_refs(
    evidence: Sequence[DiscoveryEvidence],
) -> tuple[ContentSourceReference, ...]:
    """Keep every distinct source key while preserving newest-first group order."""
    unique: dict[tuple[str, str, str | None], ContentSourceReference] = {}
    for item in evidence:
        source = item.source
        unique.setdefault((source.source_type, source.source_id, source.project_id), source)
    return tuple(unique.values())


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("content discovery limit must be between 1 and 50")
