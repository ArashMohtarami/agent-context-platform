"""Section-aware Markdown chunking without external tokenizers."""

import re
from dataclasses import dataclass
from math import ceil

from app.skills.errors import SkillChunkingError
from app.skills.types import MarkdownSection, SkillChunkDraft

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_HEADING_PATTERN = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_FENCE_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_MAX_CHUNK_CHARACTERS = 200_000
_MAX_CHUNK_TOKENS = 100_000


@dataclass(frozen=True, slots=True)
class _TextUnit:
    text: str
    is_code: bool = False


def estimate_tokens(text: str) -> int:
    """Return a deterministic tokenizer-free approximation for chunk sizing."""
    return len(_TOKEN_PATTERN.findall(text))


def split_markdown_sections(markdown: str) -> tuple[MarkdownSection, ...]:
    """Split Markdown at ATX headings while ignoring headings inside fenced code."""
    lines = markdown.splitlines()
    sections: list[MarkdownSection] = []
    current_lines: list[str] = []
    current_heading: str | None = None
    current_level: int | None = None
    current_path: tuple[str, ...] = ()
    heading_stack: list[tuple[int, str]] = []
    fence_character: str | None = None
    fence_length = 0

    def append_current() -> None:
        content = "\n".join(current_lines).strip()
        if content:
            sections.append(
                MarkdownSection(
                    heading=current_heading,
                    heading_level=current_level,
                    heading_path=current_path,
                    content=content,
                )
            )

    for line in lines:
        fence_match = _FENCE_PATTERN.match(line)
        if fence_character is not None:
            current_lines.append(line)
            stripped = line.strip()
            if stripped and set(stripped) == {fence_character} and len(stripped) >= fence_length:
                fence_character = None
                fence_length = 0
            continue
        if fence_match is not None:
            marker = fence_match.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            current_lines.append(line)
            continue

        heading_match = _HEADING_PATTERN.match(line)
        if heading_match is None:
            current_lines.append(line)
            continue

        append_current()
        level = len(heading_match.group(1))
        title = re.sub(r"[ \t]+#+[ \t]*$", "", heading_match.group(2)).strip()
        if not title or len(title) > 500:
            raise SkillChunkingError("Markdown headings must contain at most 500 characters")
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, title))
        current_lines = [line]
        current_heading = title
        current_level = level
        current_path = tuple(item[1] for item in heading_stack)

    if fence_character is not None:
        raise SkillChunkingError("Markdown contains an unterminated fenced code block")
    append_current()
    if not sections:
        raise SkillChunkingError("Markdown body does not contain chunkable content")
    return tuple(sections)


def _split_markdown_blocks(markdown: str) -> tuple[_TextUnit, ...]:
    lines = markdown.splitlines()
    units: list[_TextUnit] = []
    regular_lines: list[str] = []
    code_lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0

    def append_regular() -> None:
        text = "\n".join(regular_lines).strip()
        if text:
            units.append(_TextUnit(text))
        regular_lines.clear()

    for line in lines:
        fence_match = _FENCE_PATTERN.match(line)
        if fence_character is not None:
            code_lines.append(line)
            stripped = line.strip()
            if stripped and set(stripped) == {fence_character} and len(stripped) >= fence_length:
                units.append(_TextUnit("\n".join(code_lines).strip(), is_code=True))
                code_lines.clear()
                fence_character = None
                fence_length = 0
            continue
        if fence_match is not None:
            append_regular()
            marker = fence_match.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            code_lines = [line]
        elif not line.strip():
            append_regular()
        else:
            regular_lines.append(line)
    append_regular()
    return tuple(units)


def _split_large_unit(unit: _TextUnit, target_tokens: int) -> tuple[_TextUnit, ...]:
    token_count = estimate_tokens(unit.text)
    if unit.is_code or token_count <= target_tokens:
        return (unit,)

    words = unit.text.split()
    if not words:
        return (unit,)
    piece_count = max(2, ceil(token_count / target_tokens))
    words_per_piece = ceil(len(words) / piece_count)
    return tuple(
        _TextUnit(" ".join(words[start : start + words_per_piece]))
        for start in range(0, len(words), words_per_piece)
    )


def _tail_overlap(units: list[_TextUnit], token_budget: int) -> str:
    if token_budget <= 0:
        return ""
    selected: list[str] = []
    remaining = token_budget
    for unit in reversed(units):
        if unit.is_code:
            continue
        unit_tokens = estimate_tokens(unit.text)
        if unit_tokens <= remaining:
            selected.insert(0, unit.text)
            remaining -= unit_tokens
        else:
            words = unit.text.split()
            tail: list[str] = []
            for word in reversed(words):
                word_tokens = estimate_tokens(word)
                if word_tokens > remaining:
                    break
                tail.insert(0, word)
                remaining -= word_tokens
            if tail:
                selected.insert(0, " ".join(tail))
        if remaining == 0:
            break
    return "\n\n".join(selected)


class MarkdownSkillChunker:
    """Chunk within heading boundaries and add bounded non-code overlap."""

    def __init__(
        self,
        *,
        min_tokens: int = 500,
        target_tokens: int = 650,
        max_tokens: int = 800,
        overlap_tokens: int = 75,
    ) -> None:
        if not 1 <= min_tokens <= target_tokens <= max_tokens:
            raise ValueError("require min_tokens <= target_tokens <= max_tokens")
        if not 0 <= overlap_tokens < min_tokens:
            raise ValueError("overlap_tokens must be smaller than min_tokens")
        self._min_tokens = min_tokens
        self._target_tokens = target_tokens
        self._max_tokens = max_tokens
        self._overlap_tokens = overlap_tokens

    def chunk(self, markdown: str) -> tuple[SkillChunkDraft, ...]:
        """Create ordered chunks while preserving headings and fenced code blocks."""
        drafts: list[SkillChunkDraft] = []
        for section in split_markdown_sections(markdown):
            drafts.extend(self._chunk_section(section))
        return tuple(self._combine_adjacent_sections(drafts))

    def _combine_adjacent_sections(
        self,
        drafts: list[SkillChunkDraft],
    ) -> list[SkillChunkDraft]:
        """Pack intact short sections together when they fit under the hard maximum."""
        combined: list[SkillChunkDraft] = []
        pending: list[SkillChunkDraft] = []

        def flush() -> None:
            if not pending:
                return
            if len(pending) == 1:
                combined.append(pending[0])
                pending.clear()
                return
            content = "\n\n".join(item.content for item in pending)
            common_path = list(pending[0].heading_path)
            for item in pending[1:]:
                common_path = [
                    value
                    for index, value in enumerate(common_path)
                    if index < len(item.heading_path) and item.heading_path[index] == value
                ]
            section_names = [item.section for item in pending if item.section is not None]
            section = section_names[0] if len(section_names) == 1 else "Multiple sections"
            combined.append(
                SkillChunkDraft(
                    content=content,
                    section=section,
                    tokens=estimate_tokens(content),
                    heading_level=None,
                    heading_path=tuple(common_path),
                    overlap_tokens=0,
                )
            )
            pending.clear()

        for draft in drafts:
            if draft.overlap_tokens:
                flush()
                combined.append(draft)
                continue
            proposed = "\n\n".join([*(item.content for item in pending), draft.content])
            if pending and estimate_tokens(proposed) > self._max_tokens:
                flush()
            pending.append(draft)
        flush()
        return combined

    def _chunk_section(self, section: MarkdownSection) -> list[SkillChunkDraft]:
        lines = section.content.splitlines()
        heading_line = lines[0] if section.heading is not None else None
        body = "\n".join(lines[1:] if heading_line is not None else lines).strip()
        heading_tokens = estimate_tokens(heading_line or "")
        body_target = max(1, self._target_tokens - heading_tokens)
        units = [
            part
            for unit in _split_markdown_blocks(body)
            for part in _split_large_unit(unit, body_target)
        ]
        if not units:
            units = [_TextUnit("")]

        groups: list[list[_TextUnit]] = []
        current: list[_TextUnit] = []
        current_tokens = heading_tokens
        for unit in units:
            unit_tokens = estimate_tokens(unit.text)
            combined = current_tokens + unit_tokens
            if current and current_tokens >= self._min_tokens and combined > self._target_tokens:
                groups.append(current)
                current = []
                current_tokens = heading_tokens
                combined = current_tokens + unit_tokens
            elif current and combined > self._max_tokens:
                groups.append(current)
                current = []
                current_tokens = heading_tokens
            current.append(unit)
            current_tokens += unit_tokens
        if current:
            groups.append(current)

        if len(groups) > 1:
            final_tokens = heading_tokens + sum(estimate_tokens(unit.text) for unit in groups[-1])
            previous_tokens = heading_tokens + sum(
                estimate_tokens(unit.text) for unit in groups[-2]
            )
            if (
                final_tokens < self._min_tokens
                and previous_tokens + final_tokens <= self._max_tokens
            ):
                groups[-2].extend(groups.pop())

        drafts: list[SkillChunkDraft] = []
        for index, group in enumerate(groups):
            parts = [heading_line] if heading_line is not None else []
            overlap = ""
            if index > 0:
                base_text = "\n\n".join(part.text for part in group if part.text)
                available = self._max_tokens - heading_tokens - estimate_tokens(base_text)
                overlap = _tail_overlap(groups[index - 1], min(self._overlap_tokens, available))
                if overlap:
                    parts.append(overlap)
            parts.extend(unit.text for unit in group if unit.text)
            content = "\n\n".join(parts).strip()
            tokens = estimate_tokens(content)
            if len(content) > _MAX_CHUNK_CHARACTERS or tokens > _MAX_CHUNK_TOKENS:
                raise SkillChunkingError(
                    "an atomic Markdown block exceeds the persisted chunk limits"
                )
            drafts.append(
                SkillChunkDraft(
                    content=content,
                    section=section.heading,
                    tokens=tokens,
                    heading_level=section.heading_level,
                    heading_path=section.heading_path,
                    overlap_tokens=estimate_tokens(overlap),
                )
            )
        return drafts
