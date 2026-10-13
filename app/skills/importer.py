"""Directory-level orchestration for canonical Markdown skill imports."""

import logging
from pathlib import Path
from time import perf_counter

from app.observability.instruments import record_skill_import
from app.services.skill_service import SkillService
from app.skills.chunker import MarkdownSkillChunker
from app.skills.errors import SkillDirectoryError, SkillError, SkillParseError
from app.skills.parser import SkillParser
from app.skills.types import ParsedSkill, SkillChunkDraft, SkillImportReport

logger = logging.getLogger("agent_context.skills")


class SkillImporter:
    """Validate every discovered file before writing any skill versions."""

    def __init__(
        self,
        parser: SkillParser,
        chunker: MarkdownSkillChunker,
        service: SkillService,
    ) -> None:
        self._parser = parser
        self._chunker = chunker
        self._service = service

    def prepare_directory(
        self,
        skills_directory: Path,
    ) -> tuple[tuple[ParsedSkill, tuple[SkillChunkDraft, ...]], ...]:
        """Discover, parse, and chunk all files before persistence starts."""
        root = skills_directory.resolve()
        if not root.is_dir():
            raise SkillDirectoryError("configured skill directory does not exist")
        candidates = sorted(root.rglob("*.md"))
        prepared: list[tuple[ParsedSkill, tuple[SkillChunkDraft, ...]]] = []
        seen_skill_ids: set[str] = set()
        for candidate in candidates:
            try:
                parsed = self._parser.parse_file(candidate, root)
                chunks = self._chunker.chunk(parsed.body)
            except SkillError as error:
                relative = candidate.relative_to(root).as_posix()
                raise SkillParseError(f"{relative}: {error}") from error
            if parsed.metadata.id in seen_skill_ids:
                raise SkillParseError(
                    f"multiple canonical files declare skill id {parsed.metadata.id!r}"
                )
            seen_skill_ids.add(parsed.metadata.id)
            prepared.append((parsed, chunks))
        return tuple(prepared)

    async def import_directory(self, skills_directory: Path) -> SkillImportReport:
        """Persist every prepared skill and return deterministic statistics.

        An import usually runs in `contextctl`, which exposes no port and does
        not outlive the command, so what it did is reported as one structured
        event as well as recorded as counters. A deployment that collects
        container logs therefore has the numbers whether or not anything ever
        scraped the process that produced them.
        """
        started = perf_counter()
        prepared = self.prepare_directory(skills_directory)
        new = 0
        updated = 0
        unchanged = 0
        chunks_created = 0
        chunks_embedded = 0
        try:
            for parsed, chunks in prepared:
                outcome = await self._service.import_skill(parsed, chunks)
                if outcome.action == "new":
                    new += 1
                elif outcome.action == "updated":
                    updated += 1
                else:
                    unchanged += 1
                record_skill_import(outcome=outcome.action)
                chunks_created += outcome.chunks_created
                chunks_embedded += outcome.chunks_embedded
        except Exception:
            record_skill_import(outcome="failed")
            raise
        report = SkillImportReport(
            discovered=len(prepared),
            new=new,
            updated=updated,
            unchanged=unchanged,
            chunks_created=chunks_created,
            chunks_embedded=chunks_embedded,
        )
        logger.info(
            "skill_import_completed",
            extra={
                "operation": "skills_import",
                "component": "skills",
                "outcome": "ok",
                "result_count": report.discovered,
                "duration_ms": round((perf_counter() - started) * 1_000, 3),
            },
        )
        return report
