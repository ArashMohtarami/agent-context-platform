"""Import canonical Markdown skills into MongoDB."""

import argparse
import asyncio
import sys
from contextlib import nullcontext
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.client import MongoDBClient
from app.db.errors import SearchUnavailableError
from app.db.initializer import initialize_database
from app.db.text_indexes import ensure_text_indexes
from app.db.vector_indexes import ensure_vector_indexes
from app.embeddings.errors import EmbeddingError
from app.repositories.skill_chunk_repository import MongoSkillChunkRepository
from app.repositories.skill_repository import MongoSkillRepository
from app.security.tenant import TenantContext, current_tenant, tenant_scope
from app.security.tenant_maintenance import check_operator_context
from app.services.embedding_service import EmbeddingService, build_embedding_service
from app.services.skill_service import ChunkEmbedder, SkillService
from app.skills.chunker import MarkdownSkillChunker
from app.skills.errors import SkillError
from app.skills.importer import SkillImporter
from app.skills.parser import SkillParser
from app.skills.types import SkillImportReport


def build_parser() -> argparse.ArgumentParser:
    """Build the importer arguments."""
    parser = argparse.ArgumentParser(description="Import canonical Markdown skills")
    parser.add_argument(
        "--directory",
        type=Path,
        help="override SKILLS_DIRECTORY for this import",
    )
    parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="store chunks without vectors instead of loading the local model",
    )
    parser.add_argument("--org-id", help="explicit organization on a V6 installation")
    return parser


def build_importer(
    settings: Settings,
    mongodb: MongoDBClient,
    embedder: ChunkEmbedder | None = None,
) -> SkillImporter:
    """Compose the importer with environment-driven chunk sizing."""
    service = SkillService(
        MongoSkillRepository(mongodb.database),
        MongoSkillChunkRepository(mongodb.database),
        embedder,
    )
    return SkillImporter(
        SkillParser(max_file_bytes=settings.skill_max_file_bytes),
        MarkdownSkillChunker(
            min_tokens=settings.skill_chunk_min_tokens,
            target_tokens=settings.skill_chunk_target_tokens,
            max_tokens=settings.skill_chunk_max_tokens,
            overlap_tokens=settings.skill_chunk_overlap_tokens,
        ),
        service,
    )


async def import_skills(
    settings: Settings,
    directory: Path | None = None,
    *,
    embeddings: bool = True,
) -> SkillImportReport:
    """Connect as the application user and run one import."""
    embedding_service: EmbeddingService | None = (
        build_embedding_service(settings) if embeddings else None
    )
    mongodb = MongoDBClient(settings)
    await mongodb.connect()
    try:
        await check_operator_context(mongodb.database, enabled=settings.v6_tenant_foundation)
        await initialize_database(
            mongodb.database,
            tenant_foundation=settings.v6_tenant_foundation or current_tenant() is not None,
        )
        await ensure_text_indexes(mongodb.database)
        if embedding_service is not None:
            # Fail before any write when the configured model is unusable, and
            # keep the vector index aligned with the model that is about to run.
            info = await embedding_service.start()
            await ensure_vector_indexes(mongodb.database, dimensions=info.dimensions)
        importer = build_importer(settings, mongodb, embedding_service)
        return await importer.import_directory(directory or settings.skills_directory)
    finally:
        if embedding_service is not None:
            await embedding_service.close()
        await mongodb.close()


def _print_report(report: SkillImportReport) -> None:
    print(f"Skills discovered: {report.discovered}")
    print(f"New: {report.new}")
    print(f"Updated: {report.updated}")
    print(f"Unchanged: {report.unchanged}")
    print(f"Chunks created: {report.chunks_created}")
    print(f"Chunks embedded: {report.chunks_embedded}")


def main() -> None:
    """Run the importer and report safe, content-free errors."""
    arguments = build_parser().parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        with tenant_scope(TenantContext(arguments.org_id)) if arguments.org_id else nullcontext():
            report = asyncio.run(
                import_skills(
                    settings,
                    arguments.directory,
                    embeddings=not arguments.no_embeddings,
                )
            )
    except (SkillError, EmbeddingError, SearchUnavailableError, ValueError) as error:
        print(f"Skill import failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    _print_report(report)


if __name__ == "__main__":
    main()
