"""What deliberate knowledge changes report about themselves.

Memory is written on purpose and rarely, and a skill import is an operator
action. The rate at which either is happening — and being refused — is worth a
counter rather than only a line in a log.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId

from app.memory.errors import MemoryNotFoundError
from app.observability import instruments
from app.observability.instruments import REGISTRY
from app.skills.errors import SkillDirectoryError
from app.skills.types import SkillImportOutcome
from tests.unit.test_memory_service import build_service
from tests.unit.test_skill_importer import make_importer, markdown


@pytest.fixture(autouse=True)
def empty_registry() -> Iterator[None]:
    """Record into a registry no other test has already written to."""
    REGISTRY.reset()
    yield
    REGISTRY.reset()


def operations(operation: str, outcome: str) -> float | None:
    """Return how many memory writes of one kind ended one way."""
    return REGISTRY.value(instruments.MEMORY_OPERATIONS, operation=operation, outcome=outcome)


def imports(outcome: str) -> float | None:
    """Return how many skills an import handled one way."""
    return REGISTRY.value(instruments.SKILL_IMPORTS, outcome=outcome)


# --- memory ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_storing_a_memory_is_counted() -> None:
    service, _, _ = build_service()

    await service.create_memory(
        memory_type="convention",
        content="Use strict typing in every Python project.",
        importance=0.8,
        confidence=0.9,
        source="operator",
    )

    assert operations("create", "ok") == 1


@pytest.mark.asyncio
async def test_correcting_a_memory_is_counted_apart_from_adding_one() -> None:
    # A memory that replaces another is a correction, not an addition, and the
    # two say different things about how a deployment is being used.
    service, _, _ = build_service()
    first = await service.create_memory(
        memory_type="convention",
        content="Retries are capped at three attempts.",
        importance=0.8,
        confidence=0.9,
        source="operator",
    )

    await service.create_memory(
        memory_type="convention",
        content="Retries are capped at five attempts.",
        importance=0.8,
        confidence=0.9,
        source="operator",
        supersedes=first.id,
    )

    assert operations("create", "ok") == 1
    assert operations("supersede", "ok") == 1


@pytest.mark.asyncio
async def test_a_refused_memory_write_is_counted_as_a_failure() -> None:
    service, _, _ = build_service()

    with pytest.raises(MemoryNotFoundError):
        await service.update_memory(ObjectId(), content="something")

    assert operations("update", "failed") == 1
    assert operations("update", "ok") is None


@pytest.mark.asyncio
async def test_an_update_that_changes_nothing_is_counted_as_unchanged() -> None:
    # Distinct from both a write and a failure: an idempotent repeat is neither
    # a change to the knowledge base nor a problem.
    service, _, _ = build_service()
    stored = await service.create_memory(
        memory_type="convention",
        content="Use strict typing.",
        importance=0.8,
        confidence=0.9,
        source="operator",
    )

    await service.update_memory(stored.id)

    assert operations("update", "unchanged") == 1


@pytest.mark.asyncio
async def test_an_applied_update_is_counted_as_one() -> None:
    service, _, _ = build_service()
    stored = await service.create_memory(
        memory_type="convention",
        content="Use strict typing.",
        importance=0.8,
        confidence=0.9,
        source="operator",
    )

    await service.update_memory(stored.id, content="Use strict typing, checked with mypy.")

    assert operations("update", "ok") == 1


# --- skill imports ---------------------------------------------------------


def skills_directory(root: Path, *skill_ids: str) -> Path:
    """Write one canonical file per named skill."""
    root.mkdir(parents=True, exist_ok=True)
    for skill_id in skill_ids:
        (root / f"{skill_id}.md").write_text(markdown(skill_id), encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_an_import_counts_what_it_did_with_each_skill(tmp_path: Path) -> None:
    root = skills_directory(tmp_path / "skills", "one", "two", "three")
    service = MagicMock()
    service.import_skill = AsyncMock(
        side_effect=[
            SkillImportOutcome("new", "one", "1.0.0", 2),
            SkillImportOutcome("unchanged", "three", "1.0.0", 0),
            SkillImportOutcome("updated", "two", "1.0.0", 3),
        ]
    )

    await make_importer(service).import_directory(root)

    assert imports("new") == 1
    assert imports("updated") == 1
    assert imports("unchanged") == 1


@pytest.mark.asyncio
async def test_an_import_that_breaks_partway_counts_the_failure(tmp_path: Path) -> None:
    root = skills_directory(tmp_path / "skills", "one", "two")
    service = MagicMock()
    service.import_skill = AsyncMock(
        side_effect=[
            SkillImportOutcome("new", "one", "1.0.0", 2),
            RuntimeError("the database went away"),
        ]
    )

    with pytest.raises(RuntimeError):
        await make_importer(service).import_directory(root)

    assert imports("new") == 1
    assert imports("failed") == 1


@pytest.mark.asyncio
async def test_a_directory_that_does_not_exist_imports_nothing_and_counts_nothing(
    tmp_path: Path,
) -> None:
    # A mistake in the command rather than a skill that failed to import.
    with pytest.raises(SkillDirectoryError):
        await make_importer(MagicMock()).import_directory(tmp_path / "absent")

    assert imports("failed") is None
    assert imports("new") is None


@pytest.mark.asyncio
async def test_an_import_reports_what_it_did_as_one_structured_event(tmp_path: Path) -> None:
    # An import runs in `contextctl`, which exposes no port and does not outlive
    # the command, so the log stream it already has is how its numbers leave.
    root = skills_directory(tmp_path / "skills", "one")
    service = MagicMock()
    service.import_skill = AsyncMock(return_value=SkillImportOutcome("new", "one", "1.0.0", 2))

    with patch("app.skills.importer.logger") as logger:
        await make_importer(service).import_directory(root)

    logged = logger.info.call_args
    assert logged.args == ("skill_import_completed",)
    assert logged.kwargs["extra"]["operation"] == "skills_import"
    assert logged.kwargs["extra"]["component"] == "skills"
    assert logged.kwargs["extra"]["outcome"] == "ok"
    assert logged.kwargs["extra"]["result_count"] == 1
    assert logged.kwargs["extra"]["duration_ms"] >= 0.0
