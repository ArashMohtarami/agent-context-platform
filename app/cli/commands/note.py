"""V3 Phase 6 working-note CLI commands backed only by the REST API."""

import argparse
import json

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result, fields


def _source_reference(value: str) -> dict[str, str]:
    """Parse one explicit provenance reference; it never becomes a path or URL read."""
    try:
        parsed: object = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("--source-ref must be valid JSON") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError("--source-ref must be a JSON object")
    allowed = {"source_id", "snapshot_id", "document_id", "chunk_id"}
    if set(parsed) - allowed or "source_id" not in parsed:
        raise ValueError(
            "--source-ref requires source_id and permits snapshot_id, document_id, chunk_id"
        )
    if not all(isinstance(item, str) for item in parsed.values()):
        raise ValueError("--source-ref values must be strings")
    return parsed


async def create(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Create a working note without saving memory, context, or a candidate."""
    body = {
        "research_project_id": arguments.research_project_id,
        "note_id": arguments.note_id,
        "title": arguments.title,
        "body": arguments.body,
        "source_refs": [_source_reference(item) for item in arguments.source_ref],
        "status": arguments.status,
    }
    async with api_client(settings) as client:
        response = await request(client, "POST", "/api/v3/research/notes", json=body)
    return Result(
        data=response,
        lines=fields(
            {
                "note_id": response.get("note_id"),
                "research_project_id": response.get("research_project_id"),
                "title": response.get("title"),
                "status": response.get("status"),
                "source_refs": response.get("source_refs"),
                "created_by": response.get("created_by"),
            }
        ),
    )


def add_arguments(subparsers: SubParsers) -> None:
    """Register only the requested Phase 6 note-create command."""
    group = subparsers.add_parser(
        "note",
        help="save working research notes",
        description="Notes are research artifacts, not durable memory or promoted knowledge.",
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)
    creating = commands.add_parser("create", help="create one working research note")
    creating.add_argument("--research-project-id", required=True)
    creating.add_argument("--note-id", required=True)
    creating.add_argument("--title", required=True)
    creating.add_argument("--body", required=True)
    creating.add_argument(
        "--source-ref",
        action="append",
        default=[],
        metavar="JSON",
        help='repeatable provenance JSON, e.g. {"source_id":"provider-docs","snapshot_id":"v1"}',
    )
    creating.add_argument(
        "--status", choices=("working", "reviewed", "archived"), default="working"
    )
    creating.set_defaults(handler=create, needs_platform=True)
