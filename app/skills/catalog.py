"""Read-only discovery of canonical Git skills without MongoDB or embeddings."""

import re
from dataclasses import dataclass
from pathlib import Path

from app.skills.chunker import MarkdownSkillChunker, split_markdown_sections
from app.skills.errors import SkillDirectoryError, SkillParseError
from app.skills.parser import SkillParser
from app.skills.types import ParsedSkill

STOP_WORDS = frozenset(
    "a an and are as at be build by create for from how implement in is it make of on "
    "or the this to use using with".split()
)
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
FIELD_WEIGHTS = {"id": 14, "name": 12, "tags": 10, "description": 5, "body": 1}


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One validated source skill, with its import-ready chunk count."""

    skill: ParsedSkill
    chunk_count: int

    def summary(self) -> dict[str, object]:
        """Expose source metadata without implying an imported active version."""
        return {
            **self.skill.metadata.model_dump(),
            "source_path": self.skill.source_path,
            "content_hash": self.skill.content_hash,
            "chunks": self.chunk_count,
        }


@dataclass(frozen=True, slots=True)
class CatalogMatch:
    """Lexically ranked match; scores are local weights, not semantic confidence."""

    entry: CatalogEntry
    score: int
    sections: tuple[str, ...]


def load_catalog(root: Path, *, max_file_bytes: int = 1_000_000) -> tuple[CatalogEntry, ...]:
    """Apply the production parser and chunker before exposing any catalog entries."""
    root = root.absolute()
    if root.is_symlink():
        raise SkillDirectoryError("catalog root must not be a symlink")
    if not root.is_dir():
        raise SkillDirectoryError("configured skill directory does not exist")
    parser = SkillParser(max_file_bytes=max_file_bytes)
    chunker = MarkdownSkillChunker(
        min_tokens=500, target_tokens=650, max_tokens=800, overlap_tokens=75
    )
    seen: set[str] = set()
    entries = []
    for path in sorted(root.rglob("*.md")):
        # Check ancestor directories too: Path.rglob behavior around symlinks varies by Python.
        ancestors = (root / parent for parent in path.relative_to(root).parents)
        if any(part.is_symlink() for part in (path, *ancestors)):
            raise SkillDirectoryError("catalog paths must not contain symlinks")
        parsed = parser.parse_file(path, root)
        if parsed.metadata.id in seen:
            raise SkillParseError(
                f"multiple canonical files declare skill id {parsed.metadata.id!r}"
            )
        seen.add(parsed.metadata.id)
        entries.append(CatalogEntry(parsed, len(chunker.chunk(parsed.body))))
    return tuple(entries)


def search_terms(query: str) -> tuple[str, ...]:
    """Normalize separators and common question words, preserving technical vocabulary."""
    tokens = tuple(dict.fromkeys(_TOKEN_PATTERN.findall(query.lower())))
    return tuple(token for token in tokens if token not in STOP_WORDS)


def _term_matches(term: str, tokens: set[str]) -> bool:
    return term in tokens or (len(term) >= 3 and any(token.startswith(term) for token in tokens))


def search_catalog(
    entries: tuple[CatalogEntry, ...],
    query: str = "",
    *,
    category: str | None = None,
    tags: tuple[str, ...] = (),
    limit: int = 50,
) -> tuple[CatalogMatch, ...]:
    """Require every technical query term and every tag, then rank metadata before body text."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if len(query) > 500:
        raise ValueError("query must be at most 500 characters")
    terms = search_terms(query)
    if query.strip() and not terms:
        return ()
    matches = []
    for entry in entries:
        metadata = entry.skill.metadata
        if category and metadata.category != category:
            continue
        if not all(tag in metadata.tags for tag in tags):
            continue
        fields = {
            "id": metadata.id,
            "name": metadata.name,
            "tags": " ".join(metadata.tags),
            "description": metadata.description,
            "body": entry.skill.body,
        }
        tokens = {
            name: set(_TOKEN_PATTERN.findall(value.lower())) for name, value in fields.items()
        }
        if not all(any(_term_matches(term, value) for value in tokens.values()) for term in terms):
            continue
        score = sum(
            weight
            for term in terms
            for name, weight in FIELD_WEIGHTS.items()
            if _term_matches(term, tokens[name])
        )
        if terms and query.strip().lower() == metadata.id:
            score += 100
        sections = tuple(
            section.heading or "Introduction"
            for section in split_markdown_sections(entry.skill.body)
            if terms
            and any(
                _term_matches(term, set(_TOKEN_PATTERN.findall(section.content.lower())))
                for term in terms
            )
        )[:3]
        matches.append(CatalogMatch(entry, score, sections))
    return tuple(
        sorted(matches, key=lambda item: (-item.score, item.entry.skill.metadata.id))[:limit]
    )
