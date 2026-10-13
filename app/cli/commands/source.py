"""V3 source-registry and Phase 3 constrained-ingestion REST commands."""

import argparse
import asyncio
import json
from base64 import b64encode
from mimetypes import guess_type
from pathlib import Path

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result, fields, table
from app.research.providers import MAX_INGESTION_BYTES

_SOURCE_TYPES = (
    "manual",
    "url",
    "file",
    "markdown",
    "pdf",
    "git",
    "github",
    "documentation",
    "rss",
)
_SOURCE_CLASSES = (
    "primary",
    "official_documentation",
    "standard",
    "source_code",
    "academic",
    "secondary",
    "community",
    "user_provided",
    "generated",
    "unknown",
)
_COLUMNS = ("SOURCE", "TYPE", "CLASS", "STATUS", "TITLE")


def _metadata(value: str, option: str) -> dict[str, object]:
    """Decode an explicit JSON object; command arguments never become a shell expression."""
    try:
        parsed: object = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{option} must be valid JSON") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError(f"{option} must be a JSON object")
    return parsed


def _snapshot(arguments: argparse.Namespace) -> dict[str, object] | None:
    """Build optional immutable snapshot metadata, never reading the named source."""
    values = (
        arguments.snapshot_id,
        arguments.content_hash,
        arguments.snapshot_retrieved_at,
        arguments.version_identifier,
        arguments.snapshot_metadata_json,
    )
    if not any(value is not None for value in values):
        return None
    if (
        arguments.snapshot_id is None
        or arguments.content_hash is None
        or arguments.snapshot_retrieved_at is None
    ):
        raise ValueError(
            "--snapshot-id, --content-hash, and --snapshot-retrieved-at are required together"
        )
    return {
        "snapshot_id": arguments.snapshot_id,
        "retrieved_at": arguments.snapshot_retrieved_at,
        "content_hash": arguments.content_hash,
        "version_identifier": arguments.version_identifier,
        "metadata": (
            _metadata(arguments.snapshot_metadata_json, "--snapshot-metadata-json")
            if arguments.snapshot_metadata_json is not None
            else {}
        ),
    }


async def add(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Register metadata only; the CLI never reads a URI or repository."""
    body: dict[str, object] = {
        "research_project_id": arguments.research_project_id,
        "source_id": arguments.source_id,
        "source_type": arguments.source_type,
        "uri": arguments.uri,
        "title": arguments.title,
        "publisher": arguments.publisher,
        "author": arguments.author,
        "published_at": arguments.published_at,
        "retrieved_at": arguments.retrieved_at,
        "source_class": arguments.source_class,
        "trust_metadata": (
            _metadata(arguments.trust_metadata_json, "--trust-metadata-json")
            if arguments.trust_metadata_json is not None
            else {}
        ),
        "status": arguments.status,
    }
    snapshot = _snapshot(arguments)
    if snapshot is not None:
        body["snapshot"] = snapshot
    async with api_client(settings) as client:
        response = await request(client, "POST", "/api/v3/research/sources", json=body)
    return Result(
        data=response,
        lines=fields(
            {
                "source_id": response.get("source_id"),
                "research_project_id": response.get("research_project_id"),
                "source_type": response.get("source_type"),
                "source_class": response.get("source_class"),
                "title": response.get("title"),
                "registered_snapshot": response.get("registered_snapshot"),
            }
        ),
    )


async def list_sources(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """List authorized source metadata in the explicitly named research project."""
    async with api_client(settings) as client:
        response = await request(
            client,
            "GET",
            "/api/v3/research/sources",
            params={
                "research_project_id": arguments.research_project_id,
                "status": arguments.status,
                "source_class": arguments.source_class,
                "limit": arguments.limit,
                "cursor": arguments.cursor,
            },
        )
    items = response.get("items", [])
    lines = table(
        _COLUMNS,
        [
            (
                item.get("source_id"),
                item.get("source_type"),
                item.get("source_class"),
                item.get("status"),
                item.get("title"),
            )
            for item in items
        ],
    )
    if response.get("next_cursor"):
        lines.append(f"\nmore: --cursor {response['next_cursor']}")
    return Result(data=response, lines=lines)


async def show(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Read one source through the API authorization boundary."""
    async with api_client(settings) as client:
        response = await request(client, "GET", f"/api/v3/research/sources/{arguments.source_id}")
    return Result(
        data=response,
        lines=fields(
            {
                "source_id": response.get("source_id"),
                "research_project_id": response.get("research_project_id"),
                "source_type": response.get("source_type"),
                "uri": response.get("uri"),
                "canonical_uri": response.get("canonical_uri"),
                "title": response.get("title"),
                "source_class": response.get("source_class"),
                "trust_metadata": response.get("trust_metadata"),
                "status": response.get("status"),
                "authorization": response.get("authorization"),
                "updated_at": response.get("updated_at"),
            }
        ),
    )


def _read_upload(path: Path) -> bytes:
    """Read one operator-named regular file, bounded before it crosses the API boundary."""
    if not path.is_file():
        raise ValueError("--file must name a regular file")
    with path.open("rb") as handle:
        data = handle.read(MAX_INGESTION_BYTES + 1)
    if len(data) > MAX_INGESTION_BYTES:
        raise ValueError(f"--file may be at most {MAX_INGESTION_BYTES} bytes")
    return data


async def ingest(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Send explicit content or a source-ID-only registered acquisition request."""
    if arguments.fetch_url:
        body: dict[str, object] = {"fetch_url": True}
    elif arguments.fetch_repository:
        body = {"fetch_repository": True}
    else:
        body = {
            "content_type": arguments.content_type,
            "title": arguments.title,
            "language": arguments.language,
        }
        if arguments.text is not None:
            body["content"] = arguments.text
        else:
            path = Path(arguments.file)
            data = await asyncio.to_thread(_read_upload, path)
            body["content_base64"] = b64encode(data).decode("ascii")
            body["filename"] = path.name
            if arguments.content_type is None:
                inferred, _ = guess_type(path.name)
                if inferred is None:
                    raise ValueError(
                        "--content-type is required for an unrecognized file extension"
                    )
                body["content_type"] = inferred
        if body["content_type"] is None:
            body["content_type"] = "text/plain"
    async with api_client(settings) as client:
        response = await request(
            client,
            "POST",
            f"/api/v3/research/sources/{arguments.source_id}/ingest",
            json=body,
        )
    job = response.get("job", {})
    return Result(
        data=response,
        lines=fields(
            {
                "source_id": response.get("source_id"),
                "snapshot_id": response.get("snapshot", {}).get("snapshot_id"),
                "job_id": job.get("job_id"),
                "status": job.get("status"),
                "documents_created": job.get("documents_created"),
                "chunks_created": job.get("chunks_created"),
                "idempotent": response.get("idempotent"),
            }
        ),
    )


async def ingestion_status(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Read one persisted job through the source-scoped authorization boundary."""
    async with api_client(settings) as client:
        response = await request(
            client,
            "GET",
            f"/api/v3/research/sources/{arguments.source_id}/ingestion-jobs/{arguments.job_id}",
        )
    return Result(
        data=response,
        lines=fields(
            {
                "job_id": response.get("job_id"),
                "source_id": response.get("source_id"),
                "snapshot_id": response.get("snapshot_id"),
                "status": response.get("status"),
                "error": response.get("error"),
                "documents_created": response.get("documents_created"),
                "chunks_created": response.get("chunks_created"),
            }
        ),
    )


def add_arguments(subparsers: SubParsers) -> None:
    """Register source metadata and explicitly requested Phase 3 ingestion commands."""
    group = subparsers.add_parser(
        "source",
        help="register and inspect source metadata",
        description=(
            "Register untrusted source metadata; this command never fetches, reads, or executes it."
        ),
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)

    adding = commands.add_parser("add", help="register one source")
    adding.add_argument("--research-project-id", required=True, help="authorized research project")
    adding.add_argument("--source-id", required=True, help="stable source identifier")
    adding.add_argument("--source-type", required=True, choices=_SOURCE_TYPES)
    adding.add_argument("--uri", required=True, help="metadata URI; it is never dereferenced")
    adding.add_argument("--title", required=True)
    adding.add_argument("--publisher")
    adding.add_argument("--author")
    adding.add_argument("--published-at", help="ISO-8601 timestamp, when known")
    adding.add_argument("--retrieved-at", help="ISO-8601 timestamp, when known")
    adding.add_argument("--source-class", choices=_SOURCE_CLASSES, default="unknown")
    adding.add_argument("--trust-metadata-json", help="JSON object of descriptive metadata")
    adding.add_argument("--status", choices=("active", "archived"), default="active")
    adding.add_argument("--snapshot-id", help="stable identifier for supplied snapshot metadata")
    adding.add_argument("--content-hash", help="lowercase SHA-256 of supplied source content")
    adding.add_argument("--snapshot-retrieved-at", help="ISO-8601 observation timestamp")
    adding.add_argument(
        "--version-identifier", help="ETag, revision, tag, or commit supplied by caller"
    )
    adding.add_argument("--snapshot-metadata-json", help="JSON object of snapshot metadata")
    adding.set_defaults(handler=add, needs_platform=True)

    listing = commands.add_parser("list", help="list sources in one research project")
    listing.add_argument("--research-project-id", required=True)
    listing.add_argument("--status", choices=("active", "archived"))
    listing.add_argument("--source-class", choices=_SOURCE_CLASSES)
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--cursor")
    listing.set_defaults(handler=list_sources, needs_platform=True)

    showing = commands.add_parser("show", help="show one registered source")
    showing.add_argument("--source-id", required=True)
    showing.set_defaults(handler=show, needs_platform=True)

    ingesting = commands.add_parser(
        "ingest", help="ingest explicit text, one bounded local file, or a registered remote source"
    )
    ingesting.add_argument("--source-id", required=True)
    submitted = ingesting.add_mutually_exclusive_group(required=True)
    submitted.add_argument("--text", help="explicit manual plain text or Markdown")
    submitted.add_argument("--file", help="one local text, Markdown, or PDF file to upload")
    submitted.add_argument(
        "--fetch-url",
        action="store_true",
        help="fetch only this source's already-registered canonical HTTP(S) URI",
    )
    submitted.add_argument(
        "--fetch-repository",
        action="store_true",
        help="read only this source's already-registered public HTTPS Git repository",
    )
    ingesting.add_argument(
        "--content-type",
        choices=(
            "text/plain",
            "text/markdown",
            "text/x-markdown",
            "application/pdf",
            "application/x-pdf",
        ),
    )
    ingesting.add_argument("--title")
    ingesting.add_argument("--language", default="und")
    ingesting.set_defaults(handler=ingest, needs_platform=True)

    job_status = commands.add_parser("status", help="show one ingestion job")
    job_status.add_argument("--source-id", required=True)
    job_status.add_argument("--job-id", required=True)
    job_status.set_defaults(handler=ingestion_status, needs_platform=True)
