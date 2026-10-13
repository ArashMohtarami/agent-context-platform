"""Citation-preserving research-document chunking.

This reuses V1's tokenizer-free sizing primitive but retains exact source lines
and PDF pages. It is deliberately parsing-only: it cannot fetch or execute a
source, and it makes no statement about truth or relevance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from app.research.providers import ExtractedPage
from app.skills.chunker import estimate_tokens

_HEADING: Final = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_FENCE: Final = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
MAX_RESEARCH_CHUNK_CHARACTERS: Final = 100_000


class ResearchChunkingError(ValueError):
    """A bounded source document cannot be divided into safe retained chunks."""


@dataclass(frozen=True, slots=True)
class ResearchChunkDraft:
    """A normalized excerpt and every location the citation layer can retain."""

    content: str
    section: str | None
    heading_path: tuple[str, ...]
    page: int | None
    line_start: int | None
    line_end: int | None
    anchor: str | None
    # Repository ingestion supplies the nearest lightweight declaration. It is
    # metadata for navigation, not compiler-level semantic analysis.
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class _Segment:
    text: str
    line_start: int
    line_end: int
    section: str | None
    heading_path: tuple[str, ...]
    anchor: str | None


class ResearchDocumentChunker:
    """Bound paragraphs near V1 chunk sizes while preserving source locations."""

    def __init__(self, *, target_tokens: int = 650, max_tokens: int = 800) -> None:
        if not 1 <= target_tokens <= max_tokens:
            raise ValueError("require target_tokens <= max_tokens")
        self._target_tokens = target_tokens
        self._max_tokens = max_tokens

    def chunk(
        self, *, content_type: str, pages: tuple[ExtractedPage, ...]
    ) -> tuple[ResearchChunkDraft, ...]:
        drafts: list[ResearchChunkDraft] = []
        markdown = content_type == "text/markdown"
        for page in pages:
            segments = (
                _markdown_segments(page.text, dict(page.heading_anchors))
                if markdown
                else _plain_segments(page.text)
            )
            drafts.extend(self._pack(segments, page=page.page))
        if not drafts:
            raise ResearchChunkingError("the document does not contain chunkable text")
        return tuple(drafts)

    def _pack(
        self, segments: tuple[_Segment, ...], *, page: int | None
    ) -> list[ResearchChunkDraft]:
        chunks: list[ResearchChunkDraft] = []
        pending: list[_Segment] = []
        pending_tokens = 0

        def flush() -> None:
            nonlocal pending, pending_tokens
            if not pending:
                return
            content = "\n\n".join(segment.text for segment in pending).strip()
            if not content or len(content) > MAX_RESEARCH_CHUNK_CHARACTERS:
                raise ResearchChunkingError("a research chunk exceeds the retained-content limit")
            first, last = pending[0], pending[-1]
            chunks.append(
                ResearchChunkDraft(
                    content=content,
                    section=first.section
                    if all(item.section == first.section for item in pending)
                    else None,
                    heading_path=(
                        first.heading_path
                        if all(item.heading_path == first.heading_path for item in pending)
                        else ()
                    ),
                    page=page,
                    line_start=first.line_start,
                    line_end=last.line_end,
                    anchor=(
                        first.anchor
                        if all(item.anchor == first.anchor for item in pending)
                        else None
                    ),
                )
            )
            pending = []
            pending_tokens = 0

        for segment in segments:
            for piece in _split_large_segment(segment, self._max_tokens):
                tokens = estimate_tokens(piece.text)
                if pending and (
                    pending_tokens + tokens > self._target_tokens
                    or piece.heading_path != pending[0].heading_path
                ):
                    flush()
                pending.append(piece)
                pending_tokens += tokens
                if pending_tokens >= self._max_tokens:
                    flush()
        flush()
        return chunks


def _split_large_segment(segment: _Segment, maximum_tokens: int) -> tuple[_Segment, ...]:
    if (
        estimate_tokens(segment.text) <= maximum_tokens
        and len(segment.text) <= MAX_RESEARCH_CHUNK_CHARACTERS
    ):
        return (segment,)
    words = segment.text.split()
    if not words:
        raise ResearchChunkingError("a document segment is empty")
    pieces: list[_Segment] = []
    current: list[str] = []
    current_tokens = 0
    for word in words:
        word_tokens = estimate_tokens(word)
        if current and current_tokens + word_tokens > maximum_tokens:
            pieces.append(
                _Segment(
                    text=" ".join(current),
                    line_start=segment.line_start,
                    line_end=segment.line_end,
                    section=segment.section,
                    heading_path=segment.heading_path,
                    anchor=segment.anchor,
                )
            )
            current = []
            current_tokens = 0
        current.append(word)
        current_tokens += word_tokens
    if current:
        pieces.append(
            _Segment(
                text=" ".join(current),
                line_start=segment.line_start,
                line_end=segment.line_end,
                section=segment.section,
                heading_path=segment.heading_path,
                anchor=segment.anchor,
            )
        )
    return tuple(pieces)


def _plain_segments(text: str) -> tuple[_Segment, ...]:
    return _segments(text.splitlines(), section=None, heading_path=(), anchor=None)


def _markdown_segments(text: str, heading_anchors: dict[int, str]) -> tuple[_Segment, ...]:
    lines = text.splitlines()
    result: list[_Segment] = []
    buffer: list[str] = []
    start = 1
    section: str | None = None
    path: tuple[str, ...] = ()
    stack: list[tuple[int, str, str | None]] = []
    fenced: tuple[str, int] | None = None

    def flush() -> None:
        nonlocal buffer, start
        if buffer:
            result.extend(
                _segments(
                    buffer,
                    section=section,
                    heading_path=path,
                    anchor=stack[-1][2] if stack else None,
                    offset=start - 1,
                )
            )
        buffer = []

    for line_number, line in enumerate(lines, start=1):
        fence = _FENCE.match(line)
        if fenced is not None:
            buffer.append(line)
            if (
                fence is not None
                and fence.group(1)[0] == fenced[0]
                and len(fence.group(1)) >= fenced[1]
            ):
                fenced = None
            continue
        if fence is not None:
            buffer.append(line)
            fenced = (fence.group(1)[0], len(fence.group(1)))
            continue
        heading = _HEADING.match(line)
        if heading is None:
            buffer.append(line)
            continue
        flush()
        title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
        if not title or len(title) > 500:
            raise ResearchChunkingError("Markdown headings must contain at most 500 characters")
        level = len(heading.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title, heading_anchors.get(line_number)))
        section = title
        path = tuple(item[1] for item in stack)
        buffer = [line]
        start = line_number
    if fenced is not None:
        raise ResearchChunkingError("Markdown contains an unterminated fenced code block")
    flush()
    return tuple(result)


def _segments(
    lines: list[str],
    *,
    section: str | None,
    heading_path: tuple[str, ...],
    anchor: str | None,
    offset: int = 0,
) -> tuple[_Segment, ...]:
    result: list[_Segment] = []
    current: list[str] = []
    start: int | None = None

    def flush() -> None:
        nonlocal current, start
        text = "\n".join(current).strip()
        if text and start is not None:
            result.append(
                _Segment(
                    text=text,
                    line_start=start + offset,
                    line_end=offset + len(current) + (start - 1),
                    section=section,
                    heading_path=heading_path,
                    anchor=anchor,
                )
            )
        current = []
        start = None

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            flush()
            continue
        if start is None:
            start = line_number
        current.append(line)
    flush()
    return tuple(result)


_PYTHON_SYMBOL: Final = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_JAVASCRIPT_SYMBOL: Final = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_GO_SYMBOL: Final = re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)")
_TERRAFORM_SYMBOL: Final = re.compile(
    r'^\s*(?:resource|module|variable|output|data)\s+"([^"\s]+)"(?:\s+"([^"\s]+)")?'
)
_YAML_SYMBOL: Final = re.compile(r"^\s*kind:\s*([A-Za-z0-9_.-]+)\s*$")


class RepositoryCodeChunker:
    """Divide repository text near symbols while retaining exact line ranges.

    This is deliberately a small lexical parser.  It never imports a module,
    runs a compiler, expands a build graph, or tries to prove call/data-flow
    relationships.  Files without a recognizable declaration are still
    chunked as bounded text with their path and lines retained by the caller.
    """

    def __init__(self, *, target_tokens: int = 650, max_tokens: int = 800) -> None:
        if not 1 <= target_tokens <= max_tokens:
            raise ValueError("require target_tokens <= max_tokens")
        self._target_tokens = target_tokens
        self._max_tokens = max_tokens
        self._document_chunker = ResearchDocumentChunker(
            target_tokens=target_tokens, max_tokens=max_tokens
        )

    def chunk(
        self, *, path: str, language: str, content_type: str, content: str
    ) -> tuple[ResearchChunkDraft, ...]:
        if content_type == "text/markdown":
            return self._document_chunker.chunk(
                content_type=content_type, pages=(ExtractedPage(text=content, page=None),)
            )
        lines = content.splitlines()
        if not lines:
            raise ResearchChunkingError("repository file does not contain chunkable text")
        markers = _repository_symbols(lines, language)
        boundaries = (
            ((1, None), *markers) if markers and markers[0][0] > 1 else markers or ((1, None),)
        )
        drafts: list[ResearchChunkDraft] = []
        for index, (start, symbol) in enumerate(boundaries):
            end = boundaries[index + 1][0] - 1 if index + 1 < len(boundaries) else len(lines)
            drafts.extend(
                self._bounded_code_chunks(
                    lines=lines,
                    start=start,
                    end=end,
                    section=symbol or path,
                    symbol=symbol,
                )
            )
        if not drafts:
            raise ResearchChunkingError("repository file does not contain chunkable text")
        return tuple(drafts)

    def _bounded_code_chunks(
        self,
        *,
        lines: list[str],
        start: int,
        end: int,
        section: str,
        symbol: str | None,
    ) -> list[ResearchChunkDraft]:
        result: list[ResearchChunkDraft] = []
        pending: list[str] = []
        pending_start: int | None = None
        pending_tokens = 0

        def flush() -> None:
            nonlocal pending, pending_start, pending_tokens
            text = "\n".join(pending).strip()
            if text and pending_start is not None:
                if len(text) > MAX_RESEARCH_CHUNK_CHARACTERS:
                    raise ResearchChunkingError(
                        "a repository chunk exceeds the retained-content limit"
                    )
                result.append(
                    ResearchChunkDraft(
                        content=text,
                        section=section,
                        heading_path=(),
                        page=None,
                        line_start=pending_start,
                        line_end=pending_start + len(pending) - 1,
                        anchor=None,
                        symbol=symbol,
                    )
                )
            pending = []
            pending_start = None
            pending_tokens = 0

        for line_number in range(start, end + 1):
            line = lines[line_number - 1]
            tokens = estimate_tokens(line) if line.strip() else 1
            if pending and pending_tokens + tokens > self._target_tokens:
                flush()
            if pending_start is None:
                pending_start = line_number
            pending.append(line)
            pending_tokens += tokens
            if pending_tokens >= self._max_tokens:
                flush()
        flush()
        return result


def _repository_symbols(lines: list[str], language: str) -> tuple[tuple[int, str], ...]:
    matcher: re.Pattern[str] | None
    if language == "python":
        matcher = _PYTHON_SYMBOL
    elif language in {"javascript", "typescript", "java", "csharp", "kotlin"}:
        matcher = _JAVASCRIPT_SYMBOL
    elif language == "go":
        matcher = _GO_SYMBOL
    elif language == "terraform":
        matcher = _TERRAFORM_SYMBOL
    elif language == "yaml":
        matcher = _YAML_SYMBOL
    else:
        matcher = None
    if matcher is None:
        return ()
    markers: list[tuple[int, str]] = []
    for number, line in enumerate(lines, start=1):
        match = matcher.match(line)
        if match is None:
            continue
        parts = tuple(value for value in match.groups() if value)
        if parts:
            markers.append((number, ".".join(parts)))
    return tuple(markers)


__all__ = [
    "RepositoryCodeChunker",
    "ResearchChunkDraft",
    "ResearchChunkingError",
    "ResearchDocumentChunker",
]
