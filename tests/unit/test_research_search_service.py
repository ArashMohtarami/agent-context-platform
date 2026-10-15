"""V3 Phase 5 research retrieval policy tests without a live search coordinator."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.db.research_search import ResearchSearchScope
from app.embeddings.types import EmbeddingVector
from app.models.research import (
    CitationLocator,
    ResearchAuthorization,
    ResearchChunkDocument,
    ResearchProjectDocument,
    ResearchProjectType,
    ResearchSourceDocument,
    ResearchSourceType,
    SourceClass,
    SourceSnapshotDocument,
)
from app.repositories.research_repository import (
    ResearchChunkSearchMatch,
    ResearchClaimSearchMatch,
    ResearchNoteSearchMatch,
)
from app.retrieval.types import SourceMatches
from app.services.research_search_service import ResearchSearchFilters, ResearchSearchService

RESEARCH = "retry-evidence"
PROJECT = "payments-api"
OTHER = "private-ledger"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
OLD = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
RECENT = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def project() -> ResearchProjectDocument:
    return ResearchProjectDocument(
        research_project_id=RESEARCH,
        name="Retry evidence",
        question="What should a payment retry do?",
        type=ResearchProjectType.TECHNICAL_RESEARCH,
        related_project_ids=[PROJECT],
        authorization=ResearchAuthorization(project_ids=[PROJECT]),
    )


def source(
    source_id: str,
    *,
    source_class: SourceClass = SourceClass.PRIMARY,
    source_type: ResearchSourceType = ResearchSourceType.URL,
    publisher: str = "Example Research",
) -> ResearchSourceDocument:
    return ResearchSourceDocument(
        research_project_id=RESEARCH,
        source_id=source_id,
        source_type=source_type,
        uri=f"https://{source_id}.example.test/retries",
        canonical_uri=f"https://{source_id}.example.test/retries",
        title=f"{source_id} retry guide",
        publisher=publisher,
        published_at=OLD,
        source_class=source_class,
        authorization=ResearchAuthorization(project_ids=[PROJECT]),
    )


def snapshot(source_id: str, when: datetime) -> SourceSnapshotDocument:
    return SourceSnapshotDocument(
        research_project_id=RESEARCH,
        source_id=source_id,
        snapshot_id=f"{source_id}-snapshot",
        retrieved_at=when,
        content_hash="a" * 64,
        authorization=ResearchAuthorization(project_ids=[PROJECT]),
    )


def chunk(
    source_id: str,
    when: datetime,
    *,
    content: str,
    source_class: SourceClass = SourceClass.PRIMARY,
) -> ResearchChunkDocument:
    return ResearchChunkDocument(
        research_project_id=RESEARCH,
        source_id=source_id,
        snapshot_id=f"{source_id}-snapshot",
        document_id=f"{source_id}-document",
        chunk_id=f"{source_id}-chunk",
        chunk_index=0,
        section="Retries",
        content=content,
        source_class=source_class,
        locator=CitationLocator(url=f"https://{source_id}.example.test/retries", anchor="retries"),
        authorization=ResearchAuthorization(project_ids=[PROJECT]),
        created_at=when,
        updated_at=when,
    )


class Chunks:
    def __init__(self, matches: Sequence[ResearchChunkSearchMatch]) -> None:
        self.research_project_id = RESEARCH
        self.matches = tuple(matches)
        self.vector_scopes: list[ResearchSearchScope] = []
        self.text_scopes: list[ResearchSearchScope] = []

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        num_candidates: int,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchChunkSearchMatch, ...]:
        assert tuple(embedding) == (0.2, 0.8)
        assert num_candidates >= limit
        self.vector_scopes.append(scope)
        matches = (
            self.matches
            if not scope.source_classes
            else tuple(
                match for match in self.matches if match.chunk.source_class in scope.source_classes
            )
        )
        return matches[:limit]

    async def text_search(
        self,
        query: str,
        *,
        limit: int,
        phrase: bool,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchChunkSearchMatch, ...]:
        assert query in {
            "payment retry policy",
            "authenticate",
            "dependency injection architecture",
        }
        self.text_scopes.append(scope)
        matches = (
            self.matches
            if not scope.source_classes
            else tuple(
                match for match in self.matches if match.chunk.source_class in scope.source_classes
            )
        )
        return matches[:limit]


class EmptyNotes:
    research_project_id = RESEARCH

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        num_candidates: int,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchNoteSearchMatch, ...]:
        return ()

    async def text_search(
        self, query: str, *, limit: int, phrase: bool, scope: ResearchSearchScope
    ) -> tuple[ResearchNoteSearchMatch, ...]:
        return ()


class EmptyClaims:
    research_project_id = RESEARCH

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        num_candidates: int,
        scope: ResearchSearchScope,
    ) -> tuple[ResearchClaimSearchMatch, ...]:
        return ()

    async def text_search(
        self, query: str, *, limit: int, phrase: bool, scope: ResearchSearchScope
    ) -> tuple[ResearchClaimSearchMatch, ...]:
        return ()


class Sources:
    def __init__(self, values: Sequence[ResearchSourceDocument]) -> None:
        self.values = {item.source_id: item for item in values}

    async def get_many(self, identifiers: Sequence[str]) -> tuple[ResearchSourceDocument, ...]:
        return tuple(self.values[item] for item in identifiers if item in self.values)


class Snapshots:
    def __init__(self, values: Sequence[SourceSnapshotDocument]) -> None:
        self.values = {(item.source_id, item.snapshot_id): item for item in values}

    async def get_many(
        self, pairs: Sequence[tuple[str, str]]
    ) -> tuple[SourceSnapshotDocument, ...]:
        return tuple(self.values[item] for item in pairs if item in self.values)


class Embedder:
    async def embed_query(self, query: str) -> EmbeddingVector:
        assert query == "payment retry policy"
        return EmbeddingVector(model="local-test", dimensions=2, values=(0.2, 0.8))


class V1Retrieval:
    async def gather_sources(self, *args: object, **kwargs: object) -> SourceMatches:
        return SourceMatches()


def service(
    matches: Sequence[ResearchChunkSearchMatch],
    sources: Sequence[ResearchSourceDocument],
    snapshots: Sequence[SourceSnapshotDocument],
    *,
    embeddings: Embedder | None = None,
    v1: V1Retrieval | None = None,
) -> tuple[ResearchSearchService, Chunks]:
    chunks = Chunks(matches)
    search = ResearchSearchService(
        lambda _project: chunks,
        lambda _project: EmptyNotes(),
        lambda _project: EmptyClaims(),
        lambda _project: Sources(sources),
        lambda _project: Snapshots(snapshots),
        V1Retrieval() if v1 is None else v1,  # type: ignore[arg-type]
        embeddings,
        token_budget=500,
        clock=lambda: NOW,
    )
    return search, chunks


@pytest.mark.asyncio
async def test_lexical_search_filters_source_class_and_preserves_snapshot_provenance() -> None:
    primary = source("primary")
    generated = source("generated", source_class=SourceClass.GENERATED)
    old_snapshot = snapshot("primary", OLD)
    new_snapshot = snapshot("generated", RECENT)
    retriever, chunks = service(
        (
            ResearchChunkSearchMatch(
                chunk("primary", OLD, content="Payment retry policy preserves idempotency."), 0.8
            ),
            ResearchChunkSearchMatch(
                chunk(
                    "generated",
                    RECENT,
                    content="Generated retry commentary uses a different explanation.",
                    source_class=SourceClass.GENERATED,
                ),
                0.9,
            ),
        ),
        (primary, generated),
        (old_snapshot, new_snapshot),
    )

    result = await retriever.search(
        project=project(),
        query="payment retry policy",
        method="lexical",
        filters=ResearchSearchFilters(source_classes=(SourceClass.PRIMARY,)),
    )

    assert [item.identifier for item in result.items] == ["primary-chunk"]
    item = result.items[0]
    assert item.snapshot_id == "primary-snapshot"
    assert item.locator == {
        "url": "https://primary.example.test/retries",
        "anchor": "retries",
        "path": None,
        "page": None,
        "line_start": None,
        "line_end": None,
        "commit": None,
    }
    assert chunks.text_scopes[-1].source_classes == ("primary",)
    assert not chunks.vector_scopes


@pytest.mark.asyncio
async def test_vector_and_hybrid_search_reuse_local_embedding_and_track_methods() -> None:
    value = source("official", source_class=SourceClass.OFFICIAL_DOCUMENTATION)
    observed = snapshot("official", RECENT)
    matches = (
        ResearchChunkSearchMatch(
            chunk(
                "official",
                RECENT,
                content="Payment retry policy needs an idempotency key.",
                source_class=SourceClass.OFFICIAL_DOCUMENTATION,
            ),
            0.9,
        ),
    )
    retriever, chunks = service(matches, (value,), (observed,), embeddings=Embedder())

    vector = await retriever.search(
        project=project(), query="payment retry policy", method="vector"
    )
    hybrid = await retriever.search(
        project=project(), query="payment retry policy", method="hybrid"
    )

    assert vector.items[0].methods == ("vector",)
    assert hybrid.items[0].methods == ("vector", "lexical")
    assert len(chunks.vector_scopes) == 2
    assert len(chunks.text_scopes) == 1


@pytest.mark.asyncio
async def test_temporal_filters_and_as_of_choose_snapshot_history() -> None:
    old_source = source("old")
    recent_source = source("recent")
    retriever, _ = service(
        (
            ResearchChunkSearchMatch(
                chunk("old", OLD, content="Old policy permits controlled retry."), 0.9
            ),
            ResearchChunkSearchMatch(
                chunk("recent", RECENT, content="Recent policy requires a stable key."), 0.8
            ),
        ),
        (old_source, recent_source),
        (snapshot("old", OLD), snapshot("recent", RECENT)),
    )

    fresh = await retriever.search(
        project=project(),
        query="payment retry policy",
        method="lexical",
        filters=ResearchSearchFilters(retrieved_after=datetime(2026, 9, 25, tzinfo=UTC)),
    )
    historic = await retriever.search(
        project=project(),
        query="payment retry policy",
        method="lexical",
        filters=ResearchSearchFilters(as_of=datetime(2026, 9, 25, tzinfo=UTC)),
    )

    assert [item.identifier for item in fresh.items] == ["recent-chunk"]
    assert [item.identifier for item in historic.items] == ["old-chunk"]


@pytest.mark.asyncio
async def test_diversification_promotes_domains_and_lowers_generated_priority() -> None:
    first = source("same-one")
    second = source("same-two")
    # Two source records deliberately share a domain; diversity should put the
    # other available domain before the second matching one.
    second = second.model_copy(
        update={
            "uri": "https://same-one.example.test/other",
            "canonical_uri": "https://same-one.example.test/other",
        }
    )
    generated = source("generated", source_class=SourceClass.GENERATED)
    primary = source("primary")
    values = (first, second, generated, primary)
    observed = tuple(snapshot(item.source_id, RECENT) for item in values)
    retriever, _ = service(
        (
            ResearchChunkSearchMatch(
                chunk(
                    "same-one", RECENT, content="Alpha substantially different retry procedure one."
                ),
                1.0,
            ),
            ResearchChunkSearchMatch(
                chunk(
                    "same-two", RECENT, content="Bravo substantially different retry procedure two."
                ),
                0.99,
            ),
            ResearchChunkSearchMatch(
                chunk(
                    "generated",
                    RECENT,
                    content="Generated substantially different retry procedure three.",
                    source_class=SourceClass.GENERATED,
                ),
                0.8,
            ),
            ResearchChunkSearchMatch(
                chunk(
                    "primary",
                    RECENT,
                    content="Primary substantially different retry procedure four.",
                ),
                0.999,
            ),
        ),
        values,
        observed,
    )

    result = await retriever.search(
        project=project(), query="payment retry policy", method="lexical"
    )

    assert result.items[0].identifier == "same-one-chunk"
    assert result.items[1].identifier != "same-two-chunk"
    assert result.items.index(
        next(item for item in result.items if item.identifier == "primary-chunk")
    ) < result.items.index(
        next(item for item in result.items if item.identifier == "generated-chunk")
    )


@pytest.mark.asyncio
async def test_authorization_token_budget_and_cursor_are_enforced_without_leaking_other_scope() -> (
    None
):
    public = source("public")
    observed = snapshot("public", RECENT)
    long_content = "token " * 140
    retriever, _ = service(
        (ResearchChunkSearchMatch(chunk("public", RECENT, content=long_content), 0.9),),
        (public,),
        (observed,),
    )

    unauthorized = await retriever.search(
        project=project(),
        query="payment retry policy",
        method="lexical",
        allowed_project_ids=(OTHER,),
    )
    budgeted = await retriever.search(
        project=project(),
        query="payment retry policy",
        method="lexical",
        token_budget=100,
    )

    assert unauthorized.items == ()
    assert budgeted.items == ()
    assert budgeted.tokens_used <= budgeted.token_budget == 100
    clamped = await retriever.search(
        project=project(),
        query="payment retry policy",
        method="lexical",
        token_budget=16_001,
    )
    assert clamped.token_budget == 16_000


@pytest.mark.asyncio
async def test_search_cursor_pages_diversified_results_without_repeating_the_first_item() -> None:
    first = source("first")
    second = source("second")
    retriever, _ = service(
        (
            ResearchChunkSearchMatch(
                chunk("first", RECENT, content="First distinct payment retry policy statement."),
                1.0,
            ),
            ResearchChunkSearchMatch(
                chunk("second", RECENT, content="Second distinct payment retry policy statement."),
                0.9,
            ),
        ),
        (first, second),
        (snapshot("first", RECENT), snapshot("second", RECENT)),
    )

    first_page = await retriever.search(
        project=project(), query="payment retry policy", method="lexical", limit=1
    )
    second_page = await retriever.search(
        project=project(),
        query="payment retry policy",
        method="lexical",
        limit=1,
        cursor=first_page.next_cursor,
    )

    assert first_page.next_cursor is not None
    assert [item.identifier for item in first_page.items] == ["first-chunk"]
    assert [item.identifier for item in second_page.items] == ["second-chunk"]


@pytest.mark.asyncio
async def test_research_code_profile_uses_existing_chunk_retrieval_for_exact_symbols() -> None:
    repository = source(
        "repository", source_class=SourceClass.SOURCE_CODE, source_type=ResearchSourceType.GIT
    )
    primary = source("primary")
    retriever, chunks = service(
        (
            ResearchChunkSearchMatch(
                chunk(
                    "repository",
                    RECENT,
                    content="def authenticate(token: str) -> bool: return bool(token)",
                    source_class=SourceClass.SOURCE_CODE,
                ),
                0.9,
            ),
            ResearchChunkSearchMatch(
                chunk("primary", RECENT, content="authenticate commentary"), 0.95
            ),
        ),
        (repository, primary),
        (snapshot("repository", RECENT), snapshot("primary", RECENT)),
    )

    result = await retriever.search(
        project=project(),
        query="authenticate",
        retrieval_profile="research_code",
        method="lexical",
    )

    assert [item.identifier for item in result.items] == ["repository-chunk"]
    assert chunks.text_scopes[-1].source_classes == (SourceClass.SOURCE_CODE,)


@pytest.mark.asyncio
async def test_research_code_profile_retrieves_repository_architecture_documents() -> None:
    repository = source(
        "architecture-repository",
        source_class=SourceClass.SOURCE_CODE,
        source_type=ResearchSourceType.GIT,
    )
    retriever, _ = service(
        (
            ResearchChunkSearchMatch(
                chunk(
                    "architecture-repository",
                    RECENT,
                    content=(
                        "The service uses dependency injection architecture "
                        "to isolate repositories."
                    ),
                    source_class=SourceClass.SOURCE_CODE,
                ),
                0.9,
            ),
        ),
        (repository,),
        (snapshot("architecture-repository", RECENT),),
    )

    result = await retriever.search(
        project=project(),
        query="dependency injection architecture",
        retrieval_profile="research_code",
        method="lexical",
    )

    assert [item.identifier for item in result.items] == ["architecture-repository-chunk"]
    assert result.items[0].source_type is ResearchSourceType.GIT


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
