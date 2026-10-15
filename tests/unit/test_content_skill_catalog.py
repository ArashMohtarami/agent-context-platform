"""V2 Phase 2 canonical content skills through the existing V1 pipeline."""

from collections.abc import Sequence
from pathlib import Path
from shutil import copytree

import pytest

from app.embeddings.types import EmbeddingVector
from app.models.common import Page
from app.models.skill import LifecycleStatus, SkillDocument, SkillTrustLevel
from app.models.skill_chunk import SkillChunkDocument
from app.services.retrieval_service import RetrievalService
from app.services.skill_service import SkillService
from app.skills.chunker import MarkdownSkillChunker
from app.skills.importer import SkillImporter
from app.skills.parser import SkillParser
from app.skills.types import SkillChunkMatch, SkillLexicalMatch, SkillVersionKey

SKILLS_ROOT = Path(__file__).parents[2] / "skills"
CONTENT_ROOT = SKILLS_ROOT / "content"
EXPECTED_SKILL_IDS = frozenset(
    {
        "linkedin-writing",
        "medium-writing",
        "github-writing",
        "technical-storytelling",
        "content-hooks",
        "content-editing",
        "technical-explanation",
        "content-repurposing",
        "content-review",
        "content-ideation",
    }
)
QUERY_TARGETS = {
    "write a LinkedIn post about an engineering lesson": "linkedin-writing",
    "create a Medium architecture article": "medium-writing",
    "write GitHub architecture documentation": "github-writing",
}


class ContentSkillStore:
    """In-memory V1 storage contract for canonical-catalogue behavior."""

    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], SkillDocument] = {}
        self.chunks: dict[tuple[str, str], tuple[SkillChunkDocument, ...]] = {}

    async def get(self, skill_id: str, version: str) -> SkillDocument | None:
        return self.documents.get((skill_id, version))

    async def get_active(self, skill_id: str) -> SkillDocument | None:
        return next(
            (
                document
                for document in self.documents.values()
                if document.skill_id == skill_id and document.status == "active"
            ),
            None,
        )

    async def list(
        self,
        *,
        status: LifecycleStatus | None = None,
        category: str | None = None,
        tag: str | None = None,
        trust_level: SkillTrustLevel | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[SkillDocument]:
        _ = cursor
        selected = [
            document
            for document in self.documents.values()
            if (status is None or document.status == status)
            and (category is None or document.category == category)
            and (tag is None or tag in document.tags)
            and (trust_level is None or document.trust_level == trust_level)
        ]
        return Page(items=tuple(selected[:limit]))

    async def list_versions(self, skill_id: str, *, limit: int = 100) -> tuple[SkillDocument, ...]:
        return tuple(
            document for document in self.documents.values() if document.skill_id == skill_id
        )[:limit]

    async def list_for_version(
        self,
        skill_id: str,
        skill_version: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page[SkillChunkDocument]:
        _ = cursor
        return Page(items=self.chunks[(skill_id, skill_version)][:limit])

    async def search_active_lexical(
        self, query: str, **kwargs: object
    ) -> tuple[SkillLexicalMatch, ...]:
        _ = query, kwargs
        return ()

    async def install_version(
        self, skill: SkillDocument, chunks: Sequence[SkillChunkDocument]
    ) -> bool:
        key = (skill.skill_id, skill.version)
        if key in self.documents:
            return False
        for existing_key, document in tuple(self.documents.items()):
            if document.skill_id == skill.skill_id and document.status == "active":
                self.documents[existing_key] = document.model_copy(
                    update={"status": "superseded", "revision": document.revision + 1}
                )
        self.documents[key] = skill
        self.chunks[key] = tuple(chunks)
        return True

    async def list_version_keys(
        self,
        *,
        status: LifecycleStatus | None = "active",
        skill_id: str | None = None,
        version: str | None = None,
        category: str | None = None,
        tags: Sequence[str] = (),
        trust_level: SkillTrustLevel | None = None,
        limit: int = 500,
    ) -> tuple[SkillVersionKey, ...]:
        selected = [
            document
            for document in self.documents.values()
            if (status is None or document.status == status)
            and (skill_id is None or document.skill_id == skill_id)
            and (version is None or document.version == version)
            and (category is None or document.category == category)
            and (trust_level is None or document.trust_level == trust_level)
            and all(tag in document.tags for tag in tags)
        ]
        return tuple(
            SkillVersionKey(document.skill_id, document.version) for document in selected[:limit]
        )

    async def get_many(self, keys: Sequence[SkillVersionKey]) -> tuple[SkillDocument, ...]:
        return tuple(
            self.documents[(key.skill_id, key.version)]
            for key in keys
            if (key.skill_id, key.version) in self.documents
        )


class CatalogEmbedder:
    """Deterministically mark every imported content chunk as locally embedded."""

    async def embed_chunks(self, contents: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        return tuple(
            EmbeddingVector(model="content-catalog-test", dimensions=2, values=(1.0, 0.0))
            for _ in contents
        )


class QueryEmbedder:
    """Give each requested content-search phrase a distinct test vector."""

    def __init__(self) -> None:
        self._queries = tuple(QUERY_TARGETS)

    async def embed_query(self, text: str) -> EmbeddingVector:
        return EmbeddingVector(
            model="content-catalog-test",
            dimensions=2,
            values=(float(self._queries.index(text) + 1), 0.0),
        )


class CatalogSearch:
    """Model MongoDB search results from the imported canonical catalogue."""

    def __init__(self, store: ContentSkillStore) -> None:
        self._store = store
        self._queries = tuple(QUERY_TARGETS)

    def _match(self, target_id: str) -> SkillChunkMatch:
        document = next(
            item
            for item in self._store.documents.values()
            if item.skill_id == target_id and item.status == "active"
        )
        return SkillChunkMatch(
            chunk=self._store.chunks[(document.skill_id, document.version)][0], score=0.99
        )

    async def vector_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        num_candidates: int,
        skill_versions: Sequence[SkillVersionKey] = (),
    ) -> tuple[SkillChunkMatch, ...]:
        _ = limit, num_candidates, skill_versions
        target = QUERY_TARGETS[self._queries[int(embedding[0]) - 1]]
        return (self._match(target),)

    async def text_search(
        self,
        query: str,
        *,
        limit: int,
        phrase: bool = False,
        skill_versions: Sequence[SkillVersionKey] = (),
    ) -> tuple[SkillChunkMatch, ...]:
        _ = limit, phrase, skill_versions
        return (self._match(QUERY_TARGETS[query]),)


def importer_for(store: ContentSkillStore) -> SkillImporter:
    """Use the production parser, chunker, service, and test-local embeddings."""
    return SkillImporter(
        SkillParser(max_file_bytes=1_000_000),
        MarkdownSkillChunker(
            min_tokens=10,
            target_tokens=1_000,
            max_tokens=2_000,
            overlap_tokens=0,
        ),
        SkillService(store, store, CatalogEmbedder()),
    )


def copied_catalogue(tmp_path: Path) -> Path:
    """Give the importer a `skills/` root containing only the content catalogue."""
    root = tmp_path / "skills"
    copytree(CONTENT_ROOT, root / "content")
    return root


def test_content_skills_use_valid_canonical_v1_frontmatter_and_chunking() -> None:
    """Every requested content skill is a trusted, versioned canonical Markdown file."""
    prepared = importer_for(ContentSkillStore()).prepare_directory(SKILLS_ROOT)
    content = [item for item in prepared if item[0].source_path.startswith("skills/content/")]

    assert {parsed.metadata.id for parsed, _ in content} == EXPECTED_SKILL_IDS
    assert all(parsed.metadata.version == "1.1.0" for parsed, _ in content)
    assert all(parsed.metadata.trust_level == "trusted" for parsed, _ in content)
    assert all(chunks for _, chunks in content)


@pytest.mark.asyncio
async def test_content_skill_import_is_embedded_idempotent_and_selects_the_active_version(
    tmp_path: Path,
) -> None:
    """Use the existing importer and service without creating a second skill system."""
    store = ContentSkillStore()
    importer = importer_for(store)
    root = copied_catalogue(tmp_path)

    first = await importer.import_directory(root)
    second = await importer.import_directory(root)
    details = await SkillService(store, store).get_skill("linkedin-writing")

    assert first.discovered == first.new == len(EXPECTED_SKILL_IDS)
    assert first.chunks_created == first.chunks_embedded
    assert second.discovered == second.unchanged == len(EXPECTED_SKILL_IDS)
    assert second.chunks_created == second.chunks_embedded == 0
    assert len(store.documents) == len(EXPECTED_SKILL_IDS)
    assert details.skill.status == "active"
    assert details.skill.source_path == "skills/content/linkedin-writing.md"
    assert details.chunks.items and all(chunk.has_embedding for chunk in details.chunks.items)


@pytest.mark.asyncio
async def test_content_queries_retrieve_platform_skills_through_v1_retrieval_paths(
    tmp_path: Path,
) -> None:
    """Platform phrases select their canonical skill in each existing retrieval mode."""
    store = ContentSkillStore()
    await importer_for(store).import_directory(copied_catalogue(tmp_path))
    retrieval = RetrievalService(store, CatalogSearch(store), QueryEmbedder())

    for query, expected_skill_id in QUERY_TARGETS.items():
        vector = await retrieval.vector_search(query)
        lexical = await retrieval.lexical_search(query)
        hybrid = await retrieval.hybrid_search(query)

        assert vector[0].skill.skill_id == expected_skill_id
        assert lexical[0].skill.skill_id == expected_skill_id
        assert hybrid.items[0].metadata["skill_id"] == expected_skill_id


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing domain behavior uses an explicit test organization."""
    yield
