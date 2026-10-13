"""V3 Phase 11 review-only skill update proposal commands via the REST API."""

import argparse

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result, fields, table

_STATES = ("pending_review", "approved", "rejected", "superseded")


async def list_proposals(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """List generated patch proposals; this never reads or edits a local skill file."""
    async with api_client(settings) as client:
        response = await request(
            client,
            "GET",
            "/api/v3/skill-update-proposals",
            params={
                "research_project_id": arguments.research_project_id,
                "status": arguments.status,
                "skill_id": arguments.skill_id,
                "limit": arguments.limit,
                "cursor": arguments.cursor,
            },
        )
    lines = table(
        ("PROPOSAL", "SKILL", "VERSION", "STATUS", "TRUST"),
        [
            (
                item.get("proposal_id"),
                item.get("skill_id"),
                item.get("current_version"),
                item.get("status"),
                item.get("trust_level"),
            )
            for item in response.get("items", [])
        ],
    )
    if response.get("next_cursor"):
        lines.append(f"\nmore: --cursor {response['next_cursor']}")
    return Result(data=response, lines=lines)


async def show_proposal(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Show proposal metadata and rationale without applying its patch."""
    async with api_client(settings) as client:
        response = await request(
            client,
            "GET",
            f"/api/v3/skill-update-proposals/{arguments.proposal_id}",
            params={"research_project_id": arguments.research_project_id},
        )
    return Result(
        data=response,
        lines=fields(
            {
                "proposal_id": response.get("proposal_id"),
                "skill_id": response.get("skill_id"),
                "current_version": response.get("current_version"),
                "status": response.get("status"),
                "trust_level": response.get("trust_level"),
                "source_claim_ids": response.get("source_claim_ids"),
                "source_citation_ids": response.get("source_citation_ids"),
                "reason": response.get("reason"),
            }
        ),
    )


async def export_proposal(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Print the inert patch for an external review workflow; no file is written."""
    async with api_client(settings) as client:
        response = await request(
            client,
            "GET",
            f"/api/v3/skill-update-proposals/{arguments.proposal_id}/export",
            params={"research_project_id": arguments.research_project_id},
        )
    patch = response.get("proposed_patch", "")
    if not isinstance(patch, str):
        raise ValueError("platform returned an invalid proposed patch")
    return Result(data=response, lines=(patch,))


def add_arguments(subparsers: SubParsers) -> None:
    """Register optional Phase 11 read/export clients; none create or modify files."""
    group = subparsers.add_parser(
        "skill",
        help="inspect generated skill update proposals",
        description=(
            "Generated proposals are review data only; canonical Markdown, Git, and imported "
            "V1 skills remain unchanged."
        ),
    )
    proposal = group.add_subparsers(dest="command", metavar="COMMAND", required=True).add_parser(
        "proposal", help="list, inspect, or export inert skill update proposals"
    )
    commands = proposal.add_subparsers(dest="proposal_command", metavar="COMMAND", required=True)

    listing = commands.add_parser("list", help="list authorized generated proposals")
    listing.add_argument("--research-project-id", required=True)
    listing.add_argument("--status", choices=_STATES)
    listing.add_argument("--skill-id")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--cursor")
    listing.set_defaults(handler=list_proposals, needs_platform=True)

    showing = commands.add_parser("show", help="show one generated proposal without applying it")
    showing.add_argument("--research-project-id", required=True)
    showing.add_argument("--proposal-id", required=True)
    showing.set_defaults(handler=show_proposal, needs_platform=True)

    exporting = commands.add_parser("export", help="print one inert patch for external review")
    exporting.add_argument("--research-project-id", required=True)
    exporting.add_argument("--proposal-id", required=True)
    exporting.set_defaults(handler=export_proposal, needs_platform=True)
