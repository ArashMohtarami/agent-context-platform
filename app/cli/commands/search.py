"""The unified search command: one question, every source that can answer it."""

import argparse

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result, table
from app.retrieval.types import ALL_SOURCES

_COLUMNS = ("SCORE", "SOURCE", "PROJECT", "TITLE")


async def search(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Search every requested source and print the ranked answer."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "POST",
            "/api/v1/search",
            json={
                "query": arguments.query,
                "project_id": arguments.project_id,
                "sources": arguments.source or None,
                "tags": arguments.tag,
                "minimum_importance": arguments.minimum_importance,
                "token_budget": arguments.token_budget,
                "limit": arguments.limit,
                "explain": arguments.explain,
            },
        )
    results = body.get("results", [])
    lines = table(
        _COLUMNS,
        [
            (
                item.get("score"),
                item.get("source"),
                (item.get("metadata") or {}).get("project_id"),
                item.get("title"),
            )
            for item in results
        ],
    )
    retrieval = body.get("retrieval") or {}
    lines.append(
        f"\n{len(results)} results  "
        f"{body.get('token_count')}/{body.get('token_budget')} tokens  "
        f"{retrieval.get('retrieval_method')}  "
        f"{retrieval.get('latency_ms')}ms"
    )
    if retrieval.get("skipped_sources"):
        lines.append(f"not searched: {', '.join(retrieval['skipped_sources'])}")
    if arguments.content:
        for item in results:
            lines.extend(("", f"--- {item.get('title')} [{item.get('source')}]", ""))
            lines.append(str(item.get("content", "")))
    return Result(data=body, lines=lines)


def add_arguments(subparsers: SubParsers) -> None:
    """Register the search command."""
    command = subparsers.add_parser(
        "search",
        help="search every source at once",
        description=(
            "Search skills, a project's own recorded context, and memory together, ranked "
            "against each other and cut to a token budget. Naming no project searches global "
            "knowledge only; project context and project memory are never inherited."
        ),
        epilog=(
            "examples:\n"
            "  contextctl search 'how do we prevent duplicate payments?'\n"
            "  contextctl search 'refund rules' --project-id payment-service --content\n"
            "  contextctl --json search 'wallet:read' --source active_skill | jq .results"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    command.add_argument("query", help="the question to answer")
    command.add_argument(
        "--project-id",
        help="scope to one project; global knowledge stays visible",
    )
    command.add_argument(
        "--source",
        action="append",
        default=[],
        choices=list(ALL_SOURCES),
        help="restrict which sources answer; repeatable",
    )
    command.add_argument("--tag", action="append", default=[], metavar="TAG", help="repeatable")
    command.add_argument(
        "--minimum-importance",
        type=float,
        default=0.0,
        metavar="N",
        help="0..1, drop anything rated below it",
    )
    command.add_argument("--token-budget", type=int, help="context tokens to return at most")
    command.add_argument("--limit", type=int, default=10, help="results at most (default: 10)")
    command.add_argument(
        "--content",
        action="store_true",
        help="print each result's full text, not just the ranking",
    )
    command.add_argument(
        "--explain",
        action="store_true",
        help="include the ranking breakdown behind each result in --json output",
    )
    command.set_defaults(handler=search, needs_platform=True)
