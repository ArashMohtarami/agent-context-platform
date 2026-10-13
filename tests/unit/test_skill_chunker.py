"""Section-aware Markdown chunking tests."""

import pytest

from app.skills.chunker import MarkdownSkillChunker, split_markdown_sections
from app.skills.errors import SkillChunkingError


def test_sections_preserve_heading_hierarchy_and_ignore_code_headings() -> None:
    markdown = """# C# Service

Introduction.

## Rules

```python
# not-a-heading
print("safe")
```

### Validation

Validate input.
"""

    sections = split_markdown_sections(markdown)

    assert [section.heading for section in sections] == ["C# Service", "Rules", "Validation"]
    assert sections[-1].heading_path == ("C# Service", "Rules", "Validation")
    assert "# not-a-heading" in sections[1].content


def test_large_section_targets_expected_size_and_adds_bounded_overlap() -> None:
    words = " ".join(f"word{index}" for index in range(1_800))
    markdown = f"# Large Section\n\n{words}"
    chunker = MarkdownSkillChunker(
        min_tokens=500,
        target_tokens=650,
        max_tokens=800,
        overlap_tokens=75,
    )

    chunks = chunker.chunk(markdown)

    assert len(chunks) == 3
    assert all(500 <= chunk.tokens <= 800 for chunk in chunks)
    assert all(chunk.content.startswith("# Large Section") for chunk in chunks)
    assert chunks[0].overlap_tokens == 0
    assert all(50 <= chunk.overlap_tokens <= 100 for chunk in chunks[1:])


def test_fenced_code_block_is_never_split_or_used_as_overlap() -> None:
    before = " ".join(f"before{index}" for index in range(600))
    after = " ".join(f"after{index}" for index in range(600))
    code = "```python\n" + "\n".join(f"value_{index} = {index}" for index in range(100)) + "\n```"
    chunks = MarkdownSkillChunker().chunk(f"# Code\n\n{before}\n\n{code}\n\n{after}")

    containing_code = [chunk for chunk in chunks if "value_99 = 99" in chunk.content]
    assert len(containing_code) == 1
    assert containing_code[0].content.count("```") == 2
    assert all(chunk.content.count("```") % 2 == 0 for chunk in chunks)


def test_short_adjacent_sections_are_combined_with_headings_intact() -> None:
    chunks = MarkdownSkillChunker().chunk("# Title\n\n## Rules\n\nRule one.")

    assert len(chunks) == 1
    assert "# Title" in chunks[0].content
    assert "## Rules" in chunks[0].content
    assert chunks[0].section == "Multiple sections"


def test_unterminated_fence_is_rejected_instead_of_split() -> None:
    with pytest.raises(SkillChunkingError, match="unterminated"):
        MarkdownSkillChunker().chunk("# Broken\n\n```python\nprint('missing close')")
