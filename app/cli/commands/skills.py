"""Skill commands: what the knowledge base holds, and how it gets there.

`list` asks the API. `import` and `reindex` are maintenance: they write to
MongoDB and reconcile search indexes, which no endpoint exposes and nothing but
an operator should do, so they run the same services the application runs.
"""

import argparse
from pathlib import Path

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.errors import CliError, UnavailableError
from app.cli.import_skills import import_skills
from app.cli.output import Result, fields, table
from app.core.config import Settings
from app.db.client import MongoDBClient, MongoDBNotReadyError
from app.db.content_indexes import (
    CONTENT_TEXT_INDEXES,
    CONTENT_VECTOR_INDEXES,
    ensure_content_text_indexes,
    ensure_content_vector_indexes,
)
from app.db.context_indexes import ensure_context_text_index, ensure_context_vector_index
from app.db.errors import SearchUnavailableError
from app.db.initializer import initialize_database
from app.db.memory_indexes import ensure_memory_text_index, ensure_memory_vector_index
from app.db.research_indexes import (
    RESEARCH_TEXT_INDEXES,
    RESEARCH_VECTOR_INDEXES,
    ensure_research_text_indexes,
    ensure_research_vector_indexes,
)
from app.db.text_indexes import ensure_text_indexes
from app.db.vector_indexes import ensure_vector_indexes
from app.embeddings.errors import EmbeddingError
from app.security.tenant import current_tenant
from app.security.tenant_maintenance import check_operator_context
from app.services.embedding_service import build_embedding_service
from app.skills.errors import SkillError

_COLUMNS = ("SKILL", "VERSION", "STATUS", "CATEGORY", "TRUST", "NAME")


async def list_skills(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """List the skill versions the platform holds."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "GET",
            "/api/v1/skills",
            params={
                "status": arguments.status,
                "category": arguments.category,
                "tag": arguments.tag,
                "trust_level": arguments.trust_level,
                "limit": arguments.limit,
                "cursor": arguments.cursor,
            },
        )
    items = body.get("items", [])
    lines = table(
        _COLUMNS,
        [
            (
                item.get("skill_id"),
                item.get("version"),
                item.get("status"),
                item.get("category"),
                item.get("trust_level"),
                item.get("name"),
            )
            for item in items
        ],
    )
    if body.get("next_cursor"):
        lines.append(f"\nmore: --cursor {body['next_cursor']}")
    return Result(data=body, lines=lines)


async def import_command(arguments: argparse.Namespace, settings: Settings) -> Result:
    """Import canonical Markdown skills into MongoDB."""
    try:
        report = await import_skills(
            settings,
            arguments.directory,
            embeddings=not arguments.no_embeddings,
        )
    except MongoDBNotReadyError as error:
        raise UnavailableError(
            "MongoDB is not ready for an import",
            hint="check the platform: contextctl health",
        ) from error
    except (SkillError, EmbeddingError, SearchUnavailableError, ValueError) as error:
        raise CliError(f"the import did not complete: {error}") from error
    data = {
        "discovered": report.discovered,
        "new": report.new,
        "updated": report.updated,
        "unchanged": report.unchanged,
        "chunks_created": report.chunks_created,
        "chunks_embedded": report.chunks_embedded,
    }
    return Result(data=data, lines=fields(data))


async def reindex(arguments: argparse.Namespace, settings: Settings) -> Result:
    """Reconcile every search index against the collections and the model.

    Search indexes are derived state: they are not backed up and can always be
    rebuilt from the stored documents, which is what this does. Reconciliation
    is idempotent, so running it on a healthy deployment reports `unchanged`
    and changes nothing.
    """
    embeddings = (
        None
        if arguments.no_embeddings or arguments.database_only
        else build_embedding_service(settings)
    )
    mongodb = MongoDBClient(settings)
    try:
        # Recovery must be able to reconcile MongoDB's schema and standard
        # indexes before mongot is started. Search operations below still fail
        # clearly if the coordinator is unavailable.
        await mongodb.connect(require_search=False)
    except MongoDBNotReadyError as error:
        raise UnavailableError(
            "MongoDB is not ready to rebuild indexes",
            hint="check the platform: contextctl health",
        ) from error
    actions: dict[str, str] = {}
    try:
        await check_operator_context(mongodb.database, enabled=settings.v6_tenant_foundation)
        await initialize_database(
            mongodb.database,
            tenant_foundation=settings.v6_tenant_foundation or current_tenant() is not None,
        )
        actions["mongodb_schema_and_indexes"] = "verified"
        if arguments.database_only:
            data = {"indexes": actions, "vectors": False}
            return Result(data=data, lines=table(("INDEX", "RESULT"), sorted(actions.items())))
        actions["skill_chunk_text_index"] = await ensure_text_indexes(mongodb.database)
        actions["project_context_text_index"] = await ensure_context_text_index(mongodb.database)
        actions["memory_text_index"] = await ensure_memory_text_index(mongodb.database)
        content_text_actions = await ensure_content_text_indexes(mongodb.database)
        research_text_actions = await ensure_research_text_indexes(mongodb.database)
        actions.update(dict(zip(CONTENT_TEXT_INDEXES.values(), content_text_actions, strict=True)))
        actions.update(
            dict(zip(RESEARCH_TEXT_INDEXES.values(), research_text_actions, strict=True))
        )
        if embeddings is not None:
            # Dimensions have one source: the model that actually loaded. A
            # vector index built for anything else is unsearchable.
            info = await embeddings.start()
            dimensions = info.dimensions
            actions["skill_chunk_vector_index"] = await ensure_vector_indexes(
                mongodb.database, dimensions=dimensions
            )
            actions["project_context_vector_index"] = await ensure_context_vector_index(
                mongodb.database, dimensions=dimensions
            )
            actions["memory_vector_index"] = await ensure_memory_vector_index(
                mongodb.database, dimensions=dimensions
            )
            content_vector_actions = await ensure_content_vector_indexes(
                mongodb.database, dimensions=dimensions
            )
            research_vector_actions = await ensure_research_vector_indexes(
                mongodb.database, dimensions=dimensions
            )
            actions.update(
                dict(zip(CONTENT_VECTOR_INDEXES.values(), content_vector_actions, strict=True))
            )
            actions.update(
                dict(zip(RESEARCH_VECTOR_INDEXES.values(), research_vector_actions, strict=True))
            )
    except SearchUnavailableError as error:
        raise UnavailableError(
            "the search coordinator could not be reached",
            hint="mongot must be running and healthy",
        ) from error
    except EmbeddingError as error:
        raise CliError(
            f"the embedding model is unusable: {error}",
            hint="rebuild the lexical indexes alone with --no-embeddings",
        ) from error
    finally:
        if embeddings is not None:
            await embeddings.close()
        await mongodb.close()
    data = {"indexes": actions, "vectors": embeddings is not None}
    lines = table(("INDEX", "RESULT"), sorted(actions.items()))
    if embeddings is None:
        lines.append("\nvector indexes were skipped (--no-embeddings)")
    return Result(data=data, lines=lines)


def add_arguments(subparsers: SubParsers) -> None:
    """Register the skill commands."""
    group = subparsers.add_parser(
        "skills",
        help="inspect and maintain the skill knowledge base",
        description="Inspect and maintain the skill knowledge base.",
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)

    listing = commands.add_parser(
        "list",
        help="list skill versions",
        description="List the skill versions the platform holds.",
        epilog="example: contextctl skills list --category backend --limit 5",
    )
    listing.add_argument("--status", default="active", help="lifecycle to list (default: active)")
    listing.add_argument("--category", help="only this category")
    listing.add_argument("--tag", help="only skills carrying this tag")
    listing.add_argument("--trust-level", help="trusted, user, external, or generated")
    listing.add_argument("--limit", type=int, default=20, help="page size (default: 20)")
    listing.add_argument("--cursor", help="continue a previous page")
    listing.set_defaults(handler=list_skills, needs_platform=True)

    importing = commands.add_parser(
        "import",
        help="import canonical Markdown skills into MongoDB",
        description=(
            "Import canonical Markdown skills. Re-importing unchanged content is a no-op; "
            "a new version supersedes the previous one and keeps its history."
        ),
        epilog="example: contextctl skills import --directory ./skills",
    )
    importing.add_argument("--directory", type=Path, help="override SKILLS_DIRECTORY")
    importing.add_argument(
        "--no-embeddings",
        action="store_true",
        help="store chunks without vectors instead of loading the local model",
    )
    importing.set_defaults(handler=import_command, needs_platform=False)

    rebuilding = commands.add_parser(
        "reindex",
        help="reconcile every search index",
        description=(
            "Reconcile every configured search index against the collections and "
            "the loaded embedding model. Idempotent: an unchanged deployment reports "
            "unchanged."
        ),
        epilog="example: contextctl skills reindex --no-embeddings",
    )
    rebuilding.add_argument(
        "--no-embeddings",
        action="store_true",
        help="rebuild the lexical indexes only, without loading the model",
    )
    rebuilding.add_argument(
        "--database-only",
        action="store_true",
        help="verify MongoDB schema and standard indexes without contacting mongot",
    )
    rebuilding.set_defaults(handler=reindex, needs_platform=False)
