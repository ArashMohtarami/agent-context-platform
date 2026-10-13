"""V3 Phase 6 claim inspection commands backed only by the REST API."""

import argparse

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result, fields, table

_COLUMNS = ("CLAIM", "STATUS", "CONFIDENCE", "STATEMENT")
_STATES = (
    "unreviewed",
    "supported",
    "partially_supported",
    "contested",
    "unsupported",
    "outdated",
)


async def list_claims(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """List explicit claim assessments in one authorized research project."""
    async with api_client(settings) as client:
        response = await request(
            client,
            "GET",
            "/api/v3/research/claims",
            params={
                "research_project_id": arguments.research_project_id,
                "status": arguments.status,
                "limit": arguments.limit,
                "cursor": arguments.cursor,
            },
        )
    lines = table(
        _COLUMNS,
        [
            (
                item.get("claim_id"),
                item.get("status"),
                item.get("confidence"),
                item.get("statement"),
            )
            for item in response.get("items", [])
        ],
    )
    if response.get("next_cursor"):
        lines.append(f"\nmore: --cursor {response['next_cursor']}")
    return Result(data=response, lines=lines)


async def show(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Show one explicitly assessed claim through its authorization boundary."""
    async with api_client(settings) as client:
        response = await request(
            client,
            "GET",
            f"/api/v3/research/claims/{arguments.claim_id}",
            params={"research_project_id": arguments.research_project_id},
        )
    return Result(
        data=response,
        lines=fields(
            {
                "claim_id": response.get("claim_id"),
                "research_project_id": response.get("research_project_id"),
                "statement": response.get("statement"),
                "status": response.get("status"),
                "confidence": response.get("confidence"),
                "confidence_rationale": response.get("confidence_rationale"),
                "supporting_citations": response.get("supporting_citations"),
                "contradicting_citations": response.get("contradicting_citations"),
                "revision": response.get("revision"),
                "updated_at": response.get("updated_at"),
            }
        ),
    )


def add_arguments(subparsers: SubParsers) -> None:
    """Register only the requested Phase 6 claim inspection commands."""
    group = subparsers.add_parser(
        "claim",
        help="inspect explicit research claim assessments",
        description="Claim confidence is an explicit assessment, not a retrieval score.",
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)

    listing = commands.add_parser("list", help="list claims in one research project")
    listing.add_argument("--research-project-id", required=True)
    listing.add_argument("--status", choices=_STATES)
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--cursor")
    listing.set_defaults(handler=list_claims, needs_platform=True)

    showing = commands.add_parser("show", help="show one research claim")
    showing.add_argument("--research-project-id", required=True)
    showing.add_argument("--claim-id", required=True)
    showing.set_defaults(handler=show, needs_platform=True)
