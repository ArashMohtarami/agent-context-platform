"""Directory importer discovery, pre-validation, and reporting tests."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.skills.chunker import MarkdownSkillChunker
from app.skills.errors import SkillParseError
from app.skills.importer import SkillImporter
from app.skills.parser import SkillParser
from app.skills.types import SkillImportOutcome


def markdown(skill_id: str, version: str = "1.0.0") -> str:
    """Return a compact canonical skill file."""
    return f"""---
id: {skill_id}
name: {skill_id}
version: {version}
description: Import test skill
category: testing
trust_level: user
tags: [testing]
---

# {skill_id}

## Rule

Keep imports deterministic.
"""


def make_importer(service: MagicMock) -> SkillImporter:
    """Build an importer with small test chunk sizes."""
    return SkillImporter(
        SkillParser(max_file_bytes=10_000),
        MarkdownSkillChunker(min_tokens=10, target_tokens=20, max_tokens=30, overlap_tokens=5),
        service,
    )


@pytest.mark.asyncio
async def test_importer_reports_new_updated_and_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    for skill_id in ("new-skill", "updated-skill", "unchanged-skill"):
        (root / f"{skill_id}.md").write_text(markdown(skill_id), encoding="utf-8")
    service = MagicMock()
    service.import_skill = AsyncMock(
        side_effect=[
            SkillImportOutcome("new", "new-skill", "1.0.0", 2),
            SkillImportOutcome("unchanged", "unchanged-skill", "1.0.0", 0),
            SkillImportOutcome("updated", "updated-skill", "1.0.0", 3),
        ]
    )

    report = await make_importer(service).import_directory(root)

    assert report.discovered == 3
    assert report.new == 1
    assert report.updated == 1
    assert report.unchanged == 1
    assert report.chunks_created == 5


@pytest.mark.asyncio
async def test_all_files_are_validated_before_any_database_write(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    (root / "valid.md").write_text(markdown("valid"), encoding="utf-8")
    (root / "malformed.md").write_text("# no frontmatter", encoding="utf-8")
    service = MagicMock()
    service.import_skill = AsyncMock()

    with pytest.raises(SkillParseError, match="malformed.md"):
        await make_importer(service).import_directory(root)

    service.import_skill.assert_not_awaited()


def test_duplicate_canonical_skill_ids_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    (root / "one.md").write_text(markdown("duplicate-skill"), encoding="utf-8")
    (root / "two.md").write_text(markdown("duplicate-skill", "2.0.0"), encoding="utf-8")

    with pytest.raises(SkillParseError, match="multiple canonical files"):
        make_importer(MagicMock()).prepare_directory(root)


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
