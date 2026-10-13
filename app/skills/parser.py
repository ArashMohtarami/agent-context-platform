"""Safe parsing of canonical Markdown skills with YAML frontmatter."""

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from app.schemas.skill import SkillFrontmatter
from app.skills.errors import SkillDirectoryError, SkillParseError
from app.skills.types import ParsedSkill


class SkillParser:
    """Read bounded UTF-8 files and validate their required frontmatter."""

    def __init__(self, *, max_file_bytes: int) -> None:
        if isinstance(max_file_bytes, bool) or max_file_bytes < 1:
            raise ValueError("max_file_bytes must be a positive integer")
        self._max_file_bytes = max_file_bytes

    def parse_file(self, path: Path, skills_directory: Path) -> ParsedSkill:
        """Parse one regular file located inside the configured skill directory."""
        root = skills_directory.resolve()
        candidate = path.resolve()
        try:
            relative_path = candidate.relative_to(root)
        except ValueError as error:
            raise SkillDirectoryError("skill file is outside the configured directory") from error
        if path.is_symlink() or not candidate.is_file():
            raise SkillDirectoryError("skill path must be a regular non-symlink file")
        try:
            size = candidate.stat().st_size
        except OSError as error:
            raise SkillDirectoryError("skill file metadata cannot be read") from error
        if size > self._max_file_bytes:
            raise SkillParseError("skill file exceeds SKILL_MAX_FILE_BYTES")
        try:
            raw = candidate.read_bytes()
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as error:
            raise SkillParseError("skill file must be readable UTF-8") from error
        source_path = (Path(root.name) / relative_path).as_posix()
        return self.parse_text(text, source_path=source_path, content_hash=sha256(raw).hexdigest())

    def parse_text(
        self,
        markdown: str,
        *,
        source_path: str,
        content_hash: str | None = None,
    ) -> ParsedSkill:
        """Parse an in-memory canonical skill for tests or higher-level callers."""
        encoded = markdown.encode("utf-8")
        if len(encoded) > self._max_file_bytes:
            raise SkillParseError("skill file exceeds SKILL_MAX_FILE_BYTES")
        lines = markdown.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise SkillParseError("skill must start with YAML frontmatter")

        closing_index = next(
            (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        if closing_index is None:
            raise SkillParseError("skill frontmatter is not terminated")

        frontmatter_text = "".join(lines[1:closing_index])
        body = "".join(lines[closing_index + 1 :]).strip()
        if not body:
            raise SkillParseError("skill Markdown body must not be empty")
        try:
            loaded: object = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as error:
            raise SkillParseError("skill frontmatter is invalid YAML") from error
        if not isinstance(loaded, Mapping) or any(not isinstance(key, str) for key in loaded):
            raise SkillParseError("skill frontmatter must be a string-keyed mapping")
        try:
            metadata = SkillFrontmatter.model_validate(dict(loaded))
        except ValidationError as error:
            raise SkillParseError("skill frontmatter failed validation") from error

        return ParsedSkill(
            metadata=metadata,
            body=body,
            source_path=source_path,
            content_hash=content_hash or sha256(encoded).hexdigest(),
        )
