"""Skill lifecycle service tests independent of MongoDB."""

from collections.abc import Sequence

import pytest

from app.embeddings.errors import EmbeddingCompatibilityError
from app.embeddings.types import EmbeddingVector
from app.models.common import Page
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument
from app.schemas.skill import SkillFrontmatter
from app.services.skill_service import SkillService
from app.skills.errors import SkillNotFoundError, SkillVersionConflictError
from app.skills.types import ParsedSkill, SkillChunkDraft, SkillLexicalMatch


class FakeSkillRepository:
    """Small version store implementing the service protocol for lifecycle tests."""

    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], SkillDocument] = {}
        self.chunks: dict[tuple[str, str], tuple[SkillChunkDocument, ...]] = {}

    async def get(self, skill_id: str, version: str) -> SkillDocument | None:
        return self.documents.get((skill_id, version))

    async def get_active(self, skill_id: str) -> SkillDocument | None:
        return next(
            (
                document
                for (stored_id, _), document in self.documents.items()
                if stored_id == skill_id and document.status == "active"
            ),
            None,
        )

    async def list(self, **kwargs: object) -> Page[SkillDocument]:
        _ = kwargs
        return Page(items=tuple(self.documents.values()))

    async def list_versions(
        self,
        skill_id: str,
        *,
        limit: int = 100,
    ) -> tuple[SkillDocument, ...]:
        return tuple(
            document for (stored_id, _), document in self.documents.items() if stored_id == skill_id
        )[:limit]

    async def search_active_lexical(
        self,
        query: str,
        **kwargs: object,
    ) -> tuple[SkillLexicalMatch, ...]:
        _ = query, kwargs
        return ()

    async def install_version(
        self,
        skill: SkillDocument,
        chunks: Sequence[SkillChunkDocument],
    ) -> bool:
        key = (skill.skill_id, skill.version)
        if key in self.documents:
            return False
        for stored_key, document in tuple(self.documents.items()):
            if document.skill_id == skill.skill_id and document.status == "active":
                self.documents[stored_key] = document.model_copy(
                    update={"status": "superseded", "revision": document.revision + 1}
                )
        self.documents[key] = skill
        self.chunks[key] = tuple(chunks)
        return True


class FakeChunkRepository:
    """Read chunk pages from the lifecycle fake store."""

    def __init__(self, skills: FakeSkillRepository) -> None:
        self._skills = skills

    async def list_for_version(
        self,
        skill_id: str,
        skill_version: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[SkillChunkDocument]:
        _ = cursor
        return Page(items=self._skills.chunks.get((skill_id, skill_version), ())[:limit])


def parsed_skill(version: str, content_hash: str) -> ParsedSkill:
    """Build a validated service input."""
    return ParsedSkill(
        metadata=SkillFrontmatter(
            id="strict-python",
            name="Strict Python",
            version=version,
            description="Typed Python practices",
            category="python",
            trust_level="trusted",
            tags=["python"],
        ),
        body="# Strict Python\n\nUse strict typing.",
        source_path="skills/python/strict-python.md",
        content_hash=content_hash,
    )


def chunk_draft(content: str = "# Strict Python\n\nUse strict typing.") -> SkillChunkDraft:
    """Build one section-aware draft."""
    return SkillChunkDraft(
        content=content,
        section="Strict Python",
        tokens=8,
        heading_level=1,
        heading_path=("Strict Python",),
        overlap_tokens=0,
    )


@pytest.mark.asyncio
async def test_import_is_idempotent_and_new_version_supersedes_old() -> None:
    repository = FakeSkillRepository()
    service = SkillService(repository, FakeChunkRepository(repository))

    first = await service.import_skill(parsed_skill("1.0.0", "a" * 64), [chunk_draft()])
    repeated = await service.import_skill(parsed_skill("1.0.0", "a" * 64), [chunk_draft()])
    second = await service.import_skill(parsed_skill("1.1.0", "b" * 64), [chunk_draft()])

    assert first.action == "new"
    assert repeated.action == "unchanged"
    assert repeated.chunks_created == 0
    assert second.action == "updated"
    assert repository.documents[("strict-python", "1.0.0")].status == "superseded"
    assert repository.documents[("strict-python", "1.1.0")].status == "active"
    assert len(repository.chunks[("strict-python", "1.0.0")]) == 1
    assert len(repository.chunks[("strict-python", "1.1.0")]) == 1


@pytest.mark.asyncio
async def test_changed_content_requires_a_new_declared_version() -> None:
    repository = FakeSkillRepository()
    service = SkillService(repository, FakeChunkRepository(repository))
    await service.import_skill(parsed_skill("1.0.0", "a" * 64), [chunk_draft()])

    with pytest.raises(SkillVersionConflictError, match="declare a new version"):
        await service.import_skill(parsed_skill("1.0.0", "b" * 64), [chunk_draft("changed")])


@pytest.mark.asyncio
async def test_get_skill_returns_selected_chunks_and_history() -> None:
    repository = FakeSkillRepository()
    service = SkillService(repository, FakeChunkRepository(repository))
    await service.import_skill(parsed_skill("1.0.0", "a" * 64), [chunk_draft()])

    details = await service.get_skill("strict-python")

    assert details.skill.version == "1.0.0"
    assert len(details.versions) == 1
    assert len(details.chunks.items) == 1

    with pytest.raises(SkillNotFoundError):
        await service.get_skill("absent")


class FakeEmbedder:
    """Return one deterministic vector for each chunk in order."""

    def __init__(self, *, model: str = "stub-embedding-model", dimensions: int = 3) -> None:
        self.model = model
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        self.calls.append(list(contents))
        return tuple(
            EmbeddingVector(
                model=self.model,
                dimensions=self.dimensions,
                values=tuple(float(index) for _ in range(self.dimensions)),
            )
            for index, _ in enumerate(contents)
        )


class ShortEmbedder(FakeEmbedder):
    """Return fewer vectors than chunks to model a broken provider."""

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        return (await super().embed_chunks(contents))[:-1]


@pytest.mark.asyncio
async def test_chunks_are_stored_without_vectors_when_no_embedder_is_configured() -> None:
    repository = FakeSkillRepository()
    service = SkillService(repository, FakeChunkRepository(repository))

    outcome = await service.import_skill(parsed_skill("1.0.0", "a" * 64), [chunk_draft()])

    stored = repository.chunks[("strict-python", "1.0.0")]
    assert outcome.chunks_embedded == 0
    assert all(chunk.has_embedding is False for chunk in stored)


@pytest.mark.asyncio
async def test_imported_chunks_carry_vectors_and_model_metadata() -> None:
    repository = FakeSkillRepository()
    embedder = FakeEmbedder()
    service = SkillService(repository, FakeChunkRepository(repository), embedder)
    drafts = [chunk_draft("first chunk"), chunk_draft("second chunk")]

    outcome = await service.import_skill(parsed_skill("1.0.0", "a" * 64), drafts)

    stored = repository.chunks[("strict-python", "1.0.0")]
    assert embedder.calls == [["first chunk", "second chunk"]]
    assert outcome.chunks_created == outcome.chunks_embedded == 2
    assert [chunk.embedding for chunk in stored] == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
    assert {chunk.embedding_model for chunk in stored} == {"stub-embedding-model"}
    assert {chunk.embedding_dimensions for chunk in stored} == {3}
    assert all(chunk.embedding_generated_at is not None for chunk in stored)


@pytest.mark.asyncio
async def test_a_partial_embedding_result_stops_the_import() -> None:
    repository = FakeSkillRepository()
    service = SkillService(repository, FakeChunkRepository(repository), ShortEmbedder())

    with pytest.raises(EmbeddingCompatibilityError, match="different number of vectors"):
        await service.import_skill(
            parsed_skill("1.0.0", "a" * 64),
            [chunk_draft("first chunk"), chunk_draft("second chunk")],
        )

    assert repository.documents == {}


@pytest.mark.asyncio
async def test_an_unchanged_import_does_not_embed_again() -> None:
    repository = FakeSkillRepository()
    embedder = FakeEmbedder()
    service = SkillService(repository, FakeChunkRepository(repository), embedder)
    await service.import_skill(parsed_skill("1.0.0", "a" * 64), [chunk_draft()])

    repeated = await service.import_skill(parsed_skill("1.0.0", "a" * 64), [chunk_draft()])

    assert repeated.action == "unchanged"
    assert repeated.chunks_embedded == 0
    assert len(embedder.calls) == 1


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
