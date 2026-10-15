"""V2 Phase 4 retrieval policy, filtering, and current-representation tests."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.content.errors import ContentRetrievalUnavailableError
from app.db.content_indexes import ContentQueryFilters
from app.db.errors import SearchUnavailableError
from app.embeddings.types import EmbeddingVector
from app.models.content import ContentBriefDocument, ContentIdeaDocument, ContentItemDocument
from app.repositories.content_repository import ContentSearchMatch
from app.services.content_retrieval_service import (
    ContentRetrievalService,
    default_content_retrieval_profiles,
)

PROFILE = "personal-brand"
OTHER_PROFILE = "other-brand"
PROJECT = "payments-api"
NOW = datetime(2026, 9, 22, tzinfo=UTC)


def idea() -> ContentIdeaDocument:
    return ContentIdeaDocument(
        profile_id=PROFILE,
        idea_id="outbox-idea",
        title="The Outbox pattern avoids lost payment events",
        description="An idea about reliably delivering payment events after a database commit.",
        pillar_id="payments",
        platforms=["linkedin"],
        origin={"type": "project_context", "project_id": PROJECT},
        priority=0.9,
        tags=["payments", "outbox"],
        created_at=NOW,
        updated_at=NOW,
    )


def brief() -> ContentBriefDocument:
    return ContentBriefDocument(
        profile_id=PROFILE,
        brief_id="outbox-linkedin",
        idea_id="outbox-idea",
        platform="linkedin",
        objective="Explain why an outbox protects payment event delivery.",
        primary_message="Persist the event with the payment transaction before delivery.",
        source_refs=[
            {"source_type": "project_context", "source_id": "outbox-adr", "project_id": PROJECT}
        ],
        tags=["payments", "outbox"],
        created_at=NOW,
        updated_at=NOW,
    )


def item(*, status: str = "published", content_id: str = "outbox-post") -> ContentItemDocument:
    return ContentItemDocument(
        profile_id=PROFILE,
        content_id=content_id,
        platform="linkedin",
        content_type="post",
        title="What the Outbox pattern fixed in our payment architecture",
        body=(
            "The archived outbox reflection documents the old payment worker."
            if status == "archived"
            else (
                "The outbox stores a payment event in the same transaction "
                "before a worker delivers it."
            )
        ),
        status=status,
        pillar_id="payments",
        topic_tags=["outbox"],
        project_ids=[PROJECT],
        source_refs=[
            {"source_type": "project_context", "source_id": "outbox-adr", "project_id": PROJECT}
        ],
        tags=["payments", "outbox"],
        created_at=NOW,
        updated_at=NOW,
    )


class Repository:
    """One profile-bound search fake that records the exact repository filters."""

    def __init__(self, profile_id: str, matches: Sequence[ContentSearchMatch]) -> None:
        self.profile_id = profile_id
        self.matches = tuple(matches)
        self.vector_filters: list[ContentQueryFilters] = []
        self.text_filters: list[ContentQueryFilters] = []

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 10,
        num_candidates: int,
        filters: ContentQueryFilters,
    ) -> tuple[ContentSearchMatch, ...]:
        assert tuple(embedding) == (0.2, 0.8)
        assert num_candidates >= limit
        self.vector_filters.append(filters)
        return self.matches[:limit]

    async def text_search(
        self,
        query: str,
        *,
        limit: int = 10,
        phrase: bool = False,
        filters: ContentQueryFilters,
    ) -> tuple[ContentSearchMatch, ...]:
        assert query == "payment outbox pattern"
        assert not phrase
        self.text_filters.append(filters)
        return self.matches[:limit]


class Embedder:
    async def embed_query(self, text: str) -> EmbeddingVector:
        assert text == "payment outbox pattern"
        return EmbeddingVector(model="local-test", dimensions=2, values=(0.2, 0.8))


def service() -> tuple[ContentRetrievalService, tuple[Repository, Repository, Repository]]:
    repositories = (
        Repository(PROFILE, (ContentSearchMatch(idea(), 0.70),)),
        Repository(PROFILE, (ContentSearchMatch(brief(), 0.75),)),
        Repository(
            PROFILE,
            (
                ContentSearchMatch(item(), 0.90),
                ContentSearchMatch(
                    item(status="archived", content_id="archived-outbox-post"), 0.80
                ),
            ),
        ),
    )
    retriever = ContentRetrievalService(
        lambda _profile: repositories[0],
        lambda _profile: repositories[1],
        lambda _profile: repositories[2],
        Embedder(),
        token_budget=1_000,
        clock=lambda: NOW,
    )
    return retriever, repositories


@pytest.mark.asyncio
async def test_hybrid_content_search_reuses_embedding_and_applies_every_filter() -> None:
    retriever, repositories = service()
    filters = ContentQueryFilters(
        profile_id=PROFILE,
        platform="linkedin",
        project_id=PROJECT,
        status=None,
        pillar_id="payments",
        tags=("payments",),
    )

    result = await retriever.search(
        profile_id=PROFILE,
        query="payment outbox pattern",
        filters=filters,
        retrieval_profile="content_history",
        method="hybrid",
        limit=5,
    )

    assert result.items[0].identifier == "outbox-post"
    assert {item.identifier for item in result.items} == {
        "outbox-post",
        "outbox-linkedin",
        "outbox-idea",
        "archived-outbox-post",
    }
    assert result.items[0].methods == ("vector", "lexical")
    assert result.items[0].private_project_ids == (PROJECT,)
    assert all(repository.vector_filters == [filters] for repository in repositories)
    assert all(repository.text_filters == [filters] for repository in repositories)


@pytest.mark.asyncio
async def test_lexical_search_keeps_archived_content_searchable_without_vectors() -> None:
    retriever, repositories = service()
    filters = ContentQueryFilters(profile_id=PROFILE, status="archived")

    result = await retriever.search(
        profile_id=PROFILE,
        query="payment outbox pattern",
        filters=filters,
        method="lexical",
        limit=5,
    )

    assert {item.identifier for item in result.items} >= {"archived-outbox-post"}
    assert all(not repository.vector_filters for repository in repositories)
    assert all(repository.text_filters == [filters] for repository in repositories)


@pytest.mark.asyncio
async def test_kind_filter_searches_only_ideas_for_the_phase_five_idea_tool() -> None:
    retriever, repositories = service()

    result = await retriever.search(
        profile_id=PROFILE,
        query="payment outbox pattern",
        filters=ContentQueryFilters(profile_id=PROFILE),
        retrieval_profile="content_ideation",
        method="lexical",
        kinds=("idea",),
        limit=5,
    )

    assert [item.kind for item in result.items] == ["idea"]
    assert repositories[0].text_filters
    assert not repositories[1].text_filters
    assert not repositories[2].text_filters


@pytest.mark.asyncio
async def test_profile_mismatch_is_refused_before_any_repository_search() -> None:
    retriever, repositories = service()

    with pytest.raises(ValueError, match="requested profile"):
        await retriever.search(
            profile_id=PROFILE,
            query="payment outbox pattern",
            filters=ContentQueryFilters(profile_id=OTHER_PROFILE),
            method="lexical",
        )

    assert all(
        not repository.text_filters and not repository.vector_filters for repository in repositories
    )


@pytest.mark.asyncio
async def test_content_search_maps_unready_mongo_search_to_safe_service_error() -> None:
    class UnavailableRepository(Repository):
        async def vector_search(
            self,
            embedding: Sequence[float],
            *,
            limit: int = 10,
            num_candidates: int,
            filters: ContentQueryFilters,
        ) -> tuple[ContentSearchMatch, ...]:
            raise SearchUnavailableError("content vector index is not ready")

    repository = UnavailableRepository(PROFILE, ())
    retriever = ContentRetrievalService(
        lambda _profile: repository,
        lambda _profile: repository,
        lambda _profile: repository,
        Embedder(),
    )

    with pytest.raises(ContentRetrievalUnavailableError, match="temporarily unavailable"):
        await retriever.search(
            profile_id=PROFILE,
            query="payment outbox pattern",
            filters=ContentQueryFilters(profile_id=PROFILE),
            method="vector",
        )


def test_retrieval_profiles_are_v2_only_and_keep_configurable_creation_priorities() -> None:
    profiles = default_content_retrieval_profiles(
        creation_priorities={"memory": 0.91, "published_content": 0.71}, token_budget=900
    )

    assert set(profiles) == {
        "content_creation",
        "content_history",
        "content_ideation",
        "content_repurposing",
    }
    creation = profiles["content_creation"]
    assert creation.source_priorities["project_context"] == 1.00
    assert creation.source_priorities["memory"] == 0.91
    assert creation.source_priorities["technical_skill"] == 0.85
    assert creation.source_priorities["content_skill"] == 0.85
    assert creation.source_priorities["published_content"] == 0.71
    assert creation.source_priorities["conversation"] == 0.40


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
