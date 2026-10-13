"""Canonical skill frontmatter and Markdown parsing tests."""

from hashlib import sha256
from pathlib import Path

import pytest

from app.skills.errors import SkillDirectoryError, SkillParseError
from app.skills.parser import SkillParser


def valid_markdown(*, skill_id: str = "test-skill", version: str = "1.0.0") -> str:
    """Build a minimal valid canonical skill."""
    return f"""---
id: {skill_id}
name: Test Skill
version: {version}
description: A valid test skill.
category: testing
trust_level: user
tags:
  - python
  - testing
---

# Test Skill

## Rules

Validate every boundary.
"""


def test_parser_validates_frontmatter_and_calculates_stable_hash(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    path = root / "python" / "test.md"
    path.parent.mkdir(parents=True)
    markdown = valid_markdown()
    path.write_text(markdown, encoding="utf-8")

    parsed = SkillParser(max_file_bytes=10_000).parse_file(path, root)

    assert parsed.metadata.id == "test-skill"
    assert parsed.metadata.tags == ["python", "testing"]
    assert parsed.body.startswith("# Test Skill")
    assert parsed.source_path == "skills/python/test.md"
    assert parsed.content_hash == sha256(markdown.encode()).hexdigest()


@pytest.mark.parametrize(
    "markdown",
    [
        "# Missing frontmatter",
        "---\n- not-a-mapping\n---\n\n# Body",
        "---\nid: test\n---\n\n# Missing required metadata",
        valid_markdown().replace("version: 1.0.0", "version: latest"),
        valid_markdown().replace("version: 1.0.0", "version: 1.0.0-alpha..1"),
        valid_markdown().replace("version: 1.0.0", "version: 1.0.0-01"),
        valid_markdown().replace("  - testing", "  - python"),
        valid_markdown().replace("tags:\n", "unexpected: field\ntags:\n"),
        valid_markdown().split("# Test Skill", maxsplit=1)[0],
    ],
)
def test_parser_rejects_malformed_frontmatter_or_empty_body(markdown: str) -> None:
    parser = SkillParser(max_file_bytes=10_000)

    with pytest.raises(SkillParseError):
        parser.parse_text(markdown, source_path="skills/test.md")


def test_parser_rejects_oversized_and_symlinked_files(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    large = root / "large.md"
    large.write_text(valid_markdown(), encoding="utf-8")
    parser = SkillParser(max_file_bytes=10)
    with pytest.raises(SkillParseError, match="SKILL_MAX_FILE_BYTES"):
        parser.parse_file(large, root)

    target = tmp_path / "outside.md"
    target.write_text(valid_markdown(), encoding="utf-8")
    linked = root / "linked.md"
    linked.symlink_to(target)
    with pytest.raises(SkillDirectoryError):
        SkillParser(max_file_bytes=10_000).parse_file(linked, root)
