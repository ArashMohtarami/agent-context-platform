"""Backup and recovery against a live mongod, with the real MongoDB tools.

Nothing is stood in for here. `mongodump` writes a real archive of a real
database, `mongorestore` reads it back, and the documents are counted before
and after. What this proves that the unit tests cannot is the thing the whole
phase exists for: that a backup this platform takes actually restores.

The Compose test service is the isolated environment. Within it, every run
owns unique documents and restore verification uses nonce-prefixed temporary
collections; cleanup removes only those records and collections.
"""

import asyncio
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bson import ObjectId

from app.backup import naming
from app.backup.manifest import read as read_manifest
from app.backup.tools import MongoTools
from app.core.config import Settings, get_settings
from app.db.client import MongoDBClient
from app.models.common import MongoDocumentModel
from app.models.memory import MemoryDocument
from app.models.project import ProjectDocument
from app.models.project_context import ProjectContextDocument
from app.models.research import (
    CandidateState,
    CandidateType,
    KnowledgeCandidateDocument,
    PromotionEventDocument,
    ResearchAuthorization,
)
from app.models.skill import SkillDocument
from app.models.skill_chunk import SkillChunkDocument
from app.repositories.backup_repository import MongoBackupRepository
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.services.backup_service import BackupService
from app.skills.types import SkillVersionKey

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_MONGODB_INTEGRATION") != "1",
        reason="set RUN_MONGODB_INTEGRATION=1 inside the Compose test service",
    ),
    pytest.mark.skipif(
        shutil.which("mongodump") is None or shutil.which("mongorestore") is None,
        reason="MongoDB Database Tools are not on PATH",
    ),
]

NOW = datetime(2026, 9, 16, 13, 15, 32, tzinfo=UTC)
# What a restore has to bring back: several documents across the collections
# that carry each kind of knowledge, so a restore losing one is a failing test.
SEEDED = {
    "skills": 2,
    "skill_chunks": 3,
    "projects": 1,
    "project_context": 2,
    "memories": 4,
    "knowledge_candidates": 1,
    "promotion_events": 1,
}


def built(run_id: str, settings: Settings) -> dict[str, list[MongoDocumentModel]]:
    """Build this run's documents, one batch per collection.

    Real models rather than arbitrary dictionaries, because every collection
    carries a JSON Schema validator and refuses anything else — correctly. The
    identifiers derive from the natural keys, which the models compute, so the
    same call reproduces the same documents and the same ids.
    """
    skill_id = f"backup-test-{run_id}"
    dimensions = settings.embedding_dimensions
    return {
        "skills": [
            SkillDocument(
                skill_id=f"{skill_id}-{index}",
                version="1.0.0",
                name=f"Backup test skill {index}",
                description="Seeded by the backup integration tests",
                category="backup-test",
                trust_level="trusted",
                source_path=f"skills/backup-test/{index}.md",
                content_hash=f"{index:064d}",
            )
            for index in range(SEEDED["skills"])
        ],
        "skill_chunks": [
            SkillChunkDocument(
                skill_id=f"{skill_id}-0",
                skill_version="1.0.0",
                chunk_id=f"chunk-{index:04d}",
                chunk_index=index,
                section="Rules",
                content=f"Seeded chunk {index} for the backup tests.",
                tokens=10,
                # A vector, because "a restore does not re-embed" is the claim
                # this whole phase makes about embeddings.
                embedding=[0.25] * dimensions,
                embedding_model=settings.embedding_model,
                embedding_dimensions=dimensions,
                embedding_generated_at=NOW,
            )
            for index in range(SEEDED["skill_chunks"])
        ],
        "projects": [
            ProjectDocument(
                project_id=f"backup-test-{run_id}",
                name="Backup recovery test project",
                description="Isolated project restored by the backup integration test",
                repository="https://example.invalid/backup-recovery.git",
                environment="test",
            )
        ],
        "project_context": [
            ProjectContextDocument(
                project_id=f"backup-test-{run_id}",
                type="architecture_decision",
                title=f"Seeded context {index}",
                content=f"Seeded project context {index} for the backup tests.",
                importance=0.5,
                source=f"backup-test-{run_id}",
                external_id=f"backup-test-{run_id}-{index}",
                embedding=[0.25] * dimensions,
                embedding_model=settings.embedding_model,
                embedding_dimensions=dimensions,
                embedding_generated_at=NOW,
            )
            for index in range(SEEDED["project_context"])
        ],
        "memories": [
            MemoryDocument(
                scope="global",
                project_id=None,
                type="preference",
                content=f"Seeded memory {index} for the backup tests.",
                importance=0.5,
                confidence=0.5,
                source=f"backup-test-{run_id}",
                external_id=f"backup-test-{run_id}-{index}",
                embedding=[0.25] * dimensions,
                embedding_model=settings.embedding_model,
                embedding_dimensions=dimensions,
                embedding_generated_at=NOW,
            )
            for index in range(SEEDED["memories"])
        ],
        # The backup is a whole-MongoDB operation, so Phase 9's primary review
        # records are included without creating a separate backup path.
        "knowledge_candidates": [
            KnowledgeCandidateDocument(
                research_project_id=f"backup-research-{run_id}",
                candidate_id="backup-candidate",
                candidate_type=CandidateType.KNOWLEDGE,
                title="Backup candidate",
                content="A review-gated proposal retained for backup verification.",
                source_claim_ids=["backup-claim"],
                authorization=ResearchAuthorization(owner_key_id="backup-writer"),
            )
        ],
        "promotion_events": [
            PromotionEventDocument(
                research_project_id=f"backup-research-{run_id}",
                candidate_id="backup-candidate",
                event_id="backup-review",
                target_type=CandidateType.KNOWLEDGE,
                reviewer="backup-reviewer",
                decision=CandidateState.APPROVED,
                decided_at=NOW,
                authorization=ResearchAuthorization(owner_key_id="backup-reviewer"),
            )
        ],
    }


def identifiers(run_id: str, settings: Settings) -> dict[str, list[ObjectId]]:
    """Return the document identifiers this run owns, in each collection.

    A document keeps its identity across a backup and a restore, which is what
    makes it findable afterwards.
    """
    return {
        collection: [document.id for document in batch]
        for collection, batch in built(run_id, settings).items()
    }


@pytest.fixture
def run_id() -> str:
    """Return one identifier owned by this test's records."""
    return str(ObjectId())[-8:]


def settings_for(directory: Path) -> Settings:
    """Return the deployment's settings with this test's backup directory.

    The **configured** database, not a scratch one. The application
    authenticates as a user with `readWrite` on its own database and nothing
    else, so a test that invented a database would be testing privileges the
    platform does not have — and would fail exactly as the first live run of
    `verify --restore` did.
    """
    return get_settings().model_copy(update={"backup_directory": directory})


@asynccontextmanager
async def platform(run_id: str, directory: Path) -> AsyncIterator[tuple[BackupService, Settings]]:
    """Open a backup service, and remove only this test's records afterwards."""
    settings = settings_for(directory)
    mongodb = MongoDBClient(settings)
    await mongodb.connect(require_search=False)
    tools = MongoTools(settings, timeout_seconds=300.0)
    owned = identifiers(run_id, settings)
    try:
        repository = MongoBackupRepository(mongodb.database)
        yield BackupService(settings, tools, repository=repository), settings
    finally:
        database = mongodb.database
        for collection, ids in owned.items():
            await database[collection].delete_many({"_id": {"$in": ids}})
        # Any copies a restore check left behind if it was interrupted.
        for name in await database.list_collection_names():
            if name.startswith("restorecheck_"):
                await database.drop_collection(name)
        await mongodb.close()


async def seed(run_id: str, settings: Settings) -> None:
    """Write this run's own documents into the configured database."""
    mongodb = MongoDBClient(settings)
    await mongodb.connect(require_search=False)
    try:
        for collection, batch in built(run_id, settings).items():
            await mongodb.database[collection].insert_many(
                [document.model_dump(by_alias=True, mode="python") for document in batch]
            )
    finally:
        await mongodb.close()


async def seeded_counts(run_id: str, settings: Settings) -> dict[str, int]:
    """Return how many of this run's own documents are present."""
    mongodb = MongoDBClient(settings)
    await mongodb.connect(require_search=False)
    try:
        database = mongodb.database
        return {
            collection: await database[collection].count_documents({"_id": {"$in": ids}})
            for collection, ids in identifiers(run_id, settings).items()
        }
    finally:
        await mongodb.close()


async def restored_skill_is_searchable(run_id: str, settings: Settings) -> None:
    """Require mongot to find the restored skill lexically and by its stored vector."""
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    try:
        repository = MongoSkillChunkRepository(mongodb.database)
        version = SkillVersionKey(f"backup-test-{run_id}-0", "1.0.0")
        lexical = ()
        for _attempt in range(30):
            lexical = await repository.text_search(
                "Seeded chunk",
                limit=3,
                skill_versions=(version,),
            )
            if len(lexical) == SEEDED["skill_chunks"]:
                break
            await asyncio.sleep(1)
        assert len(lexical) == SEEDED["skill_chunks"]

        # Text and vector indexes ingest independently. Lexical visibility
        # above proves the restored documents reached mongot, but a vector
        # query can still be briefly empty while the vector index catches up.
        vector = ()
        for _attempt in range(30):
            vector = await repository.vector_search(
                [0.25] * settings.embedding_dimensions,
                limit=3,
                num_candidates=3,
                skill_versions=(version,),
            )
            if len(vector) == SEEDED["skill_chunks"]:
                break
            await asyncio.sleep(1)
        assert len(vector) == SEEDED["skill_chunks"]
        assert all(match.chunk.has_embedding for match in vector)
    finally:
        await mongodb.close()


@pytest.mark.asyncio
async def test_a_backup_captures_every_collection(run_id: str, tmp_path: Path) -> None:
    async with platform(run_id, tmp_path) as (service, settings):
        await seed(run_id, settings)

        stored = await service.create()

        assert stored.manifest is not None
        # The backup covers the whole database, so this test's own documents
        # are a floor on what each collection held, not the whole of it.
        for collection, count in SEEDED.items():
            assert stored.manifest.collections[collection] >= count
        assert stored.manifest.documents >= sum(SEEDED.values())
        assert stored.archive.stat().st_size > 0
        # Written with the checksum that will later prove it has not changed.
        assert read_manifest(tmp_path / naming.manifest_name(stored.name)) == stored.manifest


@pytest.mark.asyncio
async def test_a_backup_this_platform_took_verifies(run_id: str, tmp_path: Path) -> None:
    async with platform(run_id, tmp_path) as (service, settings):
        await seed(run_id, settings)
        stored = await service.create()

        verification = await service.verify(stored)

        assert verification.sound, verification.failures
        assert verification.checksum_matches is True
        assert verification.readable_archive is True


@pytest.mark.asyncio
async def test_a_corrupted_archive_is_caught(run_id: str, tmp_path: Path) -> None:
    # The check that finds silent corruption on disk before the night somebody
    # needs the backup.
    async with platform(run_id, tmp_path) as (service, settings):
        await seed(run_id, settings)
        stored = await service.create()
        damaged = stored.archive.read_bytes()
        stored.archive.write_bytes(damaged[: len(damaged) // 2])

        verification = await service.verify(service.get(stored.name))

        assert not verification.sound
        assert verification.checksum_matches is False


@pytest.mark.asyncio
async def test_a_backup_restores_into_isolated_collections(run_id: str, tmp_path: Path) -> None:
    # The check that makes a backup known to be restorable rather than assumed,
    # and the one the weekly schedule runs. It restores into throwaway
    # collections, counts what arrived, and removes them again — under the same
    # least-privilege role the platform uses, which is why it cannot use a
    # separate database.
    async with platform(run_id, tmp_path) as (service, settings):
        await seed(run_id, settings)
        stored = await service.create()

        verification = await service.verify(stored, restore=True)

        assert verification.sound, verification.failures
        # What came back equals what the manifest recorded, which is the check
        # that makes the backup known to restore.
        assert verification.restored_documents == verification.expected_documents
        assert verification.restored_documents >= sum(SEEDED.values())

    # The copies it restored are gone, and the real collections are untouched.
    settings = settings_for(tmp_path)
    mongodb = MongoDBClient(settings)
    await mongodb.connect(require_search=False)
    try:
        remaining = await mongodb.database.list_collection_names()
    finally:
        await mongodb.close()
    assert not any(name.startswith("restorecheck_") for name in remaining)
    assert set(SEEDED) <= set(remaining)


@pytest.mark.asyncio
async def test_a_restore_brings_back_what_was_lost(run_id: str, tmp_path: Path) -> None:
    # The whole point, end to end: data exists, is backed up, is destroyed, and
    # comes back.
    async with platform(run_id, tmp_path) as (service, settings):
        await seed(run_id, settings)
        stored = await service.create()
        before = await seeded_counts(run_id, settings)
        assert before == SEEDED

        # Destroy exactly this test's records, leaving the rest of the database
        # alone — a restore that only works by replacing everything would not
        # be a useful one to prove.
        mongodb = MongoDBClient(settings)
        await mongodb.connect(require_search=False)
        try:
            for collection, ids in identifiers(run_id, settings).items():
                await mongodb.database[collection].delete_many({"_id": {"$in": ids}})
        finally:
            await mongodb.close()
        assert await seeded_counts(run_id, settings) == dict.fromkeys(SEEDED, 0)

        # Without --drop: the archive's documents are inserted back, and
        # anything else in the database is left where it is.
        await service.restore(stored)

        assert await seeded_counts(run_id, settings) == SEEDED
        await restored_skill_is_searchable(run_id, settings)


@pytest.mark.asyncio
async def test_retention_expires_what_it_says_it_will(run_id: str, tmp_path: Path) -> None:
    async with platform(run_id, tmp_path) as (service, settings):
        # Three real backups, dated a month apart, under a policy keeping one.
        names = []
        for days in (60, 30, 0):
            dated = BackupService(
                service._settings,
                MongoTools(settings, timeout_seconds=300.0),
                repository=None,
                clock=lambda days=days: NOW - timedelta(days=days),
            )
            names.append((await dated.create(prune=False)).name)

        narrow = service._settings.model_copy(
            update={"backup_retain_daily": 1, "backup_retain_weekly": 0, "backup_retain_monthly": 0}
        )
        pruning = BackupService(
            narrow,
            MongoTools(settings, timeout_seconds=300.0),
            repository=None,
            clock=lambda: NOW,
        )
        decision = pruning.prune()

        assert set(decision.keep) == {names[-1]}
        assert set(decision.remove) == set(names[:-1])
        assert [item.name for item in pruning.stored()] == [names[-1]]
        # The manifests went with their archives.
        assert not any(
            path.name.endswith(".manifest.json") and naming.manifest_name(names[0]) == path.name
            for path in tmp_path.iterdir()
        )
