"""Memory commands: what has been remembered on purpose."""

import argparse

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result, table

_COLUMNS = ("SCORE", "SCOPE", "PROJECT", "TYPE", "SUMMARY")


async def search(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Search the memory this request is allowed to see."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "POST",
            "/api/v1/memory/search",
            json={
                "query": arguments.query,
                "project_id": arguments.project_id,
                "scopes": arguments.scope or None,
                "types": arguments.type,
                "tags": arguments.tag,
                "status": arguments.status,
                "minimum_importance": arguments.minimum_importance,
                "limit": arguments.limit,
            },
        )
    results = body.get("results", [])
    rows = []
    for item in results:
        memory = item.get("memory") or {}
        rows.append(
            (
                item.get("score"),
                memory.get("scope"),
                memory.get("project_id"),
                memory.get("type"),
                memory.get("summary") or memory.get("content"),
            )
        )
    lines = table(_COLUMNS, rows)
    lines.append(f"\n{len(results)} memories")
    if arguments.content:
        for item in results:
            memory = item.get("memory") or {}
            lines.extend(("", f"--- {memory.get('summary') or memory.get('type')}", ""))
            lines.append(str(memory.get("content", "")))
    return Result(data=body, lines=lines)


def add_arguments(subparsers: SubParsers) -> None:
    """Register the memory commands."""
    group = subparsers.add_parser(
        "memory",
        help="search durable memory",
        description="Search what has been remembered on purpose.",
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)

    searching = commands.add_parser(
        "search",
        help="search global and project memory",
        description=(
            "Search memory. Naming a project searches its memory and global memory together; "
            "naming none searches global memory alone. Archived and superseded memory is "
            "excluded unless asked for."
        ),
        epilog=(
            "examples:\n"
            "  contextctl memory search 'typing conventions'\n"
            "  contextctl memory search 'refund review' --project-id payment-service\n"
            "  contextctl memory search 'deploys' --status superseded"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    searching.add_argument("query", help="the question to answer")
    searching.add_argument("--project-id", help="also search this project's own memory")
    searching.add_argument(
        "--scope",
        action="append",
        default=[],
        choices=["global", "project"],
        help="restrict the scope; repeatable, requires --project-id for project",
    )
    searching.add_argument("--type", action="append", default=[], metavar="TYPE", help="repeatable")
    searching.add_argument("--tag", action="append", default=[], metavar="TAG", help="repeatable")
    searching.add_argument(
        "--status",
        default="active",
        choices=["active", "archived", "superseded"],
        help="lifecycle to search (default: active)",
    )
    searching.add_argument(
        "--minimum-importance",
        type=float,
        default=0.0,
        metavar="N",
        help="0..1, drop anything rated below it",
    )
    searching.add_argument("--limit", type=int, default=10, help="results at most (default: 10)")
    searching.add_argument(
        "--content",
        action="store_true",
        help="print each memory in full, not just the ranking",
    )
    searching.set_defaults(handler=search, needs_platform=True)
