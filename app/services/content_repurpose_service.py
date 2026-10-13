"""V2 Phase 6 structured repurposing plans without prose generation.

This service receives an already-authorized V2 source record, preserves its
provenance, and uses content retrieval only to show related history.  It never
returns a target body, writes a draft, or publishes anything.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from app.content.repurposing import PLATFORM_REPURPOSE_METADATA, source_examples, source_points
from app.db.content_indexes import ContentQueryFilters
from app.models.content import (
    ContentBriefDocument,
    ContentIdeaDocument,
    ContentItemDocument,
    ContentPlatform,
    ContentSourceReference,
)
from app.models.skill import SkillDocument
from app.services.content_discovery_service import SkillReference
from app.services.content_novelty_service import ContentHistorySearch
from app.services.content_retrieval_service import (
    ContentSearchItem,
    ContentSearchKind,
    ContentSearchMethod,
)

RepurposeSourceKind = Literal["idea", "brief", "content"]
type RepurposeSource = ContentIdeaDocument | ContentBriefDocument | ContentItemDocument


class ActiveSkillLookup(Protocol):
    """Read the reviewed canonical skill selected for a target platform."""

    async def get_active(self, skill_id: str) -> SkillDocument | None: ...


@dataclass(frozen=True, slots=True)
class RelatedContent:
    """A compact prior record used as planning context, with provenance intact."""

    kind: ContentSearchKind
    identifier: str
    title: str
    platforms: tuple[str, ...]
    status: str
    source_refs: tuple[dict[str, str | None], ...]


@dataclass(frozen=True, slots=True)
class RepurposeTargetPlan:
    """Non-generative adaptation inputs for one selected destination."""

    platform: ContentPlatform
    what_to_shorten: tuple[str, ...]
    what_requires_expansion: tuple[str, ...]
    target_platform_differences: tuple[str, ...]
    target_content_skill: SkillReference | None
    previous_related_content: tuple[RelatedContent, ...]


@dataclass(frozen=True, slots=True)
class RepurposePlan:
    """A bounded source-backed plan; it contains no generated target body."""

    profile_id: str
    source_kind: RepurposeSourceKind
    source_id: str
    source_platform: ContentPlatform | None
    core_thesis: str
    reusable_supporting_points: tuple[str, ...]
    examples: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]
    targets: tuple[RepurposeTargetPlan, ...]


@dataclass(frozen=True, slots=True)
class _SourceMaterial:
    kind: RepurposeSourceKind
    identifier: str
    platform: ContentPlatform | None
    core_thesis: str
    supporting_points: tuple[str, ...]
    examples: tuple[str, ...]
    source_refs: tuple[ContentSourceReference, ...]


class ContentRepurposeService:
    """Build platform-specific planning data around reviewed source records."""

    def __init__(self, retrieval: ContentHistorySearch, skills: ActiveSkillLookup) -> None:
        self._retrieval = retrieval
        self._skills = skills

    async def prepare_repurpose_plan(
        self,
        *,
        source: RepurposeSource,
        targets: Sequence[ContentPlatform],
        method: ContentSearchMethod = "hybrid",
        limit: int = 5,
        token_budget: int | None = None,
        allowed_project_ids: frozenset[str] | None = None,
    ) -> RepurposePlan:
        """Return structure an agent may adapt after explicit human review.

        The function does not accept a target body by design.  Its only text
        fields are a source thesis and bounded source excerpts; platform
        writing guidance stays in the selected canonical Markdown skill.
        """
        if not 1 <= limit <= 50:
            raise ValueError("repurpose plan limit must be between 1 and 50")
        normalized_targets = tuple(targets)
        if not normalized_targets:
            raise ValueError("repurpose plans require at least one target platform")
        if len(normalized_targets) != len(set(normalized_targets)):
            raise ValueError("repurpose target platforms must be unique")
        material = _source_material(source)
        history = await self._retrieval.search(
            profile_id=source.profile_id,
            query=material.core_thesis,
            filters=ContentQueryFilters(profile_id=source.profile_id),
            retrieval_profile="content_repurposing",
            method=method,
            limit=min(50, limit + 1),
            token_budget=token_budget,
        )
        related = tuple(
            _related_content(item)
            for item in history.items
            if _is_related_visible(item, material, allowed_project_ids)
        )[:limit]
        target_plans: tuple[RepurposeTargetPlan, ...] = tuple(
            await asyncio.gather(
                *(self._target_plan(platform, related) for platform in normalized_targets)
            )
        )
        return RepurposePlan(
            profile_id=source.profile_id,
            source_kind=material.kind,
            source_id=material.identifier,
            source_platform=material.platform,
            core_thesis=material.core_thesis,
            reusable_supporting_points=material.supporting_points,
            examples=material.examples,
            source_refs=material.source_refs,
            targets=target_plans,
        )

    async def _target_plan(
        self,
        platform: ContentPlatform,
        related: tuple[RelatedContent, ...],
    ) -> RepurposeTargetPlan:
        metadata = PLATFORM_REPURPOSE_METADATA[platform]
        skill = await self._skills.get_active(metadata.content_skill_id)
        reference = _trusted_content_skill(skill, metadata.content_skill_id)
        return RepurposeTargetPlan(
            platform=platform,
            what_to_shorten=metadata.shorten,
            what_requires_expansion=metadata.expand,
            target_platform_differences=metadata.differences,
            target_content_skill=reference,
            previous_related_content=related,
        )


def _source_material(source: RepurposeSource) -> _SourceMaterial:
    if isinstance(source, ContentIdeaDocument):
        refs = tuple(
            ContentSourceReference(
                source_type=source.origin.type,
                source_id=source_id,
                project_id=source.origin.project_id,
            )
            for source_id in source.origin.source_ids
        )
        return _SourceMaterial(
            kind="idea",
            identifier=source.idea_id,
            platform=source.platforms[0] if len(source.platforms) == 1 else None,
            core_thesis=source.title,
            supporting_points=source_points(source.description),
            examples=source_examples(source.description),
            source_refs=refs
            or _self_reference("content_idea", source.idea_id, source.origin.project_id),
        )
    if isinstance(source, ContentBriefDocument):
        return _SourceMaterial(
            kind="brief",
            identifier=source.brief_id,
            platform=source.platform,
            core_thesis=source.primary_message,
            supporting_points=tuple(source.supporting_points) or source_points(source.objective),
            examples=source_examples("\n".join(source.supporting_points)),
            source_refs=tuple(source.source_refs)
            or _self_reference("content_brief", source.brief_id, None),
        )
    return _SourceMaterial(
        kind="content",
        identifier=source.content_id,
        platform=source.platform,
        core_thesis=source.title,
        supporting_points=source_points(source.body),
        examples=source_examples(source.body),
        source_refs=tuple(source.source_refs)
        or tuple(
            reference
            for project_id in source.project_ids
            for reference in _self_reference("content_item", source.content_id, project_id)
        )
        or _self_reference("content_item", source.content_id, None),
    )


def _self_reference(
    source_type: str, source_id: str, project_id: str | None
) -> tuple[ContentSourceReference, ...]:
    return (
        ContentSourceReference(
            source_type=source_type,
            source_id=source_id,
            project_id=project_id,
        ),
    )


def _is_related_visible(
    item: ContentSearchItem,
    source: _SourceMaterial,
    allowed_project_ids: frozenset[str] | None,
) -> bool:
    if item.kind == source.kind and item.identifier == source.identifier:
        return False
    if allowed_project_ids is None:
        return True
    return all(project_id in allowed_project_ids for project_id in item.private_project_ids)


def _related_content(item: ContentSearchItem) -> RelatedContent:
    return RelatedContent(
        kind=item.kind,
        identifier=item.identifier,
        title=item.title,
        platforms=item.platforms,
        status=item.status,
        source_refs=item.source_refs,
    )


def _trusted_content_skill(skill: SkillDocument | None, skill_id: str) -> SkillReference | None:
    if (
        skill is None
        or skill.skill_id != skill_id
        or skill.status != "active"
        or skill.trust_level != "trusted"
        or skill.category != "content"
    ):
        return None
    return SkillReference(skill.skill_id, skill.name, skill.version)
