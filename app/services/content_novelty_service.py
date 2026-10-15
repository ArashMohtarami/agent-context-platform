"""V2 Phase 6 advisory content-novelty planning service.

The service reuses the existing content retrieval engine for candidate history,
then adds a small deterministic comparison that is explainable to a human or
agent.  It neither generates content nor treats a similarity score as truth.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.content.novelty import ContentOverlap, NoveltyClassification, compare_content
from app.db.content_indexes import ContentQueryFilters
from app.models.content import ContentPlatform
from app.security.tenant_service import tenant_service
from app.services.content_retrieval_service import (
    ContentRetrievalProfileName,
    ContentSearchItem,
    ContentSearchKind,
    ContentSearchMethod,
    ContentSearchResult,
)


class ContentHistorySearch(Protocol):
    """Narrow retrieval surface required for novelty planning."""

    async def search(
        self,
        *,
        profile_id: str,
        query: str,
        filters: ContentQueryFilters,
        retrieval_profile: ContentRetrievalProfileName = ...,
        method: ContentSearchMethod = ...,
        limit: int = ...,
        token_budget: int | None = ...,
    ) -> ContentSearchResult: ...


@dataclass(frozen=True, slots=True)
class SimilarContent:
    """A compact, attributable history item supporting an advisory result."""

    kind: ContentSearchKind
    identifier: str
    title: str
    platforms: tuple[str, ...]
    status: str
    pillar_id: str | None
    project_ids: tuple[str, ...]
    source_refs: tuple[dict[str, str | None], ...]
    retrieval_score: float
    topic_overlap: float
    angle_overlap: float
    shared_topic_terms: tuple[str, ...]
    shared_angle_terms: tuple[str, ...]
    classification: NoveltyClassification


@dataclass(frozen=True, slots=True)
class ContentNoveltyResult:
    """Evidence for a novelty judgment; it is intentionally non-authoritative."""

    profile_id: str
    similar_content: tuple[SimilarContent, ...]
    topic_overlap: float
    angle_overlap: float
    classification: NoveltyClassification
    advisory: bool = True


@tenant_service
class ContentNoveltyService:
    """Search history and return transparent overlap evidence without a model call."""

    def __init__(self, retrieval: ContentHistorySearch) -> None:
        self._retrieval = retrieval

    async def check_content_novelty(
        self,
        *,
        profile_id: str,
        title: str,
        description: str,
        tags: Sequence[str] = (),
        pillar_id: str | None = None,
        platform: ContentPlatform | None = None,
        project_id: str | None = None,
        method: ContentSearchMethod = "hybrid",
        limit: int = 10,
        token_budget: int | None = None,
        allowed_project_ids: frozenset[str] | None = None,
    ) -> ContentNoveltyResult:
        """Return history-backed similarity evidence for a proposed topic.

        ``allowed_project_ids`` is an authorization input, not a search hint.
        A project-derived result is removed before overlap computation when
        the caller cannot access every project retained in its provenance.
        """
        if not 1 <= limit <= 50:
            raise ValueError("content novelty limit must be between 1 and 50")
        query = " ".join(part for part in (title, *tags) if part).strip()
        history = await self._retrieval.search(
            profile_id=profile_id,
            query=query,
            filters=ContentQueryFilters(
                profile_id=profile_id,
                platform=platform,
                project_id=project_id,
                pillar_id=pillar_id,
                tags=tuple(tags),
            ),
            retrieval_profile="content_history",
            method=method,
            limit=limit,
            token_budget=token_budget,
        )
        candidates = tuple(
            self._similar_content(
                item,
                title=title,
                description=description,
                tags=tuple(tags),
                pillar_id=pillar_id,
            )
            for item in history.items
            if _item_is_allowed(item, allowed_project_ids)
        )
        # A lexical/vector result with no independently explainable term
        # overlap is not shown as "similar".  This avoids presenting ranking
        # score as a claim of objective topical equivalence.
        similar = tuple(
            sorted(
                (
                    item
                    for item in candidates
                    if item.topic_overlap > 0.0 or item.angle_overlap > 0.0
                ),
                key=lambda item: (
                    _classification_rank(item.classification),
                    item.topic_overlap + item.angle_overlap,
                    item.retrieval_score,
                    item.identifier,
                ),
                reverse=True,
            )
        )
        if not similar:
            return ContentNoveltyResult(
                profile_id=profile_id,
                similar_content=(),
                topic_overlap=0.0,
                angle_overlap=0.0,
                classification="new-topic",
            )
        best = similar[0]
        return ContentNoveltyResult(
            profile_id=profile_id,
            similar_content=similar,
            topic_overlap=best.topic_overlap,
            angle_overlap=best.angle_overlap,
            classification=best.classification,
        )

    @staticmethod
    def _similar_content(
        item: ContentSearchItem,
        *,
        title: str,
        description: str,
        tags: tuple[str, ...],
        pillar_id: str | None,
    ) -> SimilarContent:
        overlap: ContentOverlap = compare_content(
            candidate_title=title,
            candidate_detail=description,
            candidate_tags=tags,
            candidate_pillar_id=pillar_id,
            stored_title=item.title,
            stored_detail=item.content,
            stored_tags=item.tags,
            stored_pillar_id=item.pillar_id,
        )
        return SimilarContent(
            kind=item.kind,
            identifier=item.identifier,
            title=item.title,
            platforms=item.platforms,
            status=item.status,
            pillar_id=item.pillar_id,
            project_ids=item.project_ids,
            source_refs=item.source_refs,
            retrieval_score=item.score,
            topic_overlap=overlap.topic_overlap,
            angle_overlap=overlap.angle_overlap,
            shared_topic_terms=overlap.shared_topic_terms,
            shared_angle_terms=overlap.shared_angle_terms,
            classification=overlap.classification,
        )


def _item_is_allowed(item: ContentSearchItem, allowed_project_ids: frozenset[str] | None) -> bool:
    if allowed_project_ids is None:
        return True
    return all(project_id in allowed_project_ids for project_id in item.private_project_ids)


def _classification_rank(classification: NoveltyClassification) -> int:
    return {
        "same-topic-same-angle": 4,
        "same-topic-new-angle": 3,
        "related-topic": 2,
        "new-topic": 1,
    }[classification]
