"""V3 Phase 10 review-gated knowledge-candidate commands via the REST API only."""

import argparse

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result, fields, table

_STATES = (
    "pending_review",
    "approved",
    "rejected",
    "needs_changes",
    "promoted",
    "superseded",
)


async def list_candidates(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """List the proposals this credential may inspect; this never promotes a candidate."""
    async with api_client(settings) as client:
        response = await request(
            client,
            "GET",
            "/api/v3/knowledge-candidates",
            params={
                "research_project_id": arguments.research_project_id,
                "status": arguments.status,
                "target_project_id": arguments.target_project_id,
                "limit": arguments.limit,
                "cursor": arguments.cursor,
            },
        )
    lines = table(
        ("CANDIDATE", "STATUS", "TYPE", "TARGET PROJECT", "TITLE"),
        [
            (
                item.get("candidate_id"),
                item.get("status"),
                item.get("candidate_type"),
                item.get("target_project_id"),
                item.get("title"),
            )
            for item in response.get("items", [])
        ],
    )
    if response.get("next_cursor"):
        lines.append(f"\nmore: --cursor {response['next_cursor']}")
    return Result(data=response, lines=lines)


async def approve(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Record explicit approval only; Phase 9 does not promote it to a V1 target."""
    payload = {
        "research_project_id": arguments.research_project_id,
        "expected_revision": arguments.expected_revision,
        "reason": arguments.reason,
    }
    async with api_client(settings) as client:
        response = await request(
            client,
            "POST",
            f"/api/v3/knowledge-candidates/{arguments.candidate_id}/approve",
            json=payload,
        )
    record = response.get("candidate", {})
    return Result(
        data=response,
        lines=fields(
            {
                "candidate_id": record.get("candidate_id"),
                "status": record.get("status"),
                "reviewer": record.get("reviewer"),
                "reviewed_at": record.get("reviewed_at"),
                "promotion_event_id": response.get("event", {}).get("event_id"),
                "promoted": False,
            }
        ),
    )


async def reject(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Reject or request changes; neither decision mutates V1 knowledge."""
    payload = {
        "research_project_id": arguments.research_project_id,
        "expected_revision": arguments.expected_revision,
        "reason": arguments.reason,
        "decision": "needs_changes" if arguments.needs_changes else "rejected",
    }
    async with api_client(settings) as client:
        response = await request(
            client,
            "POST",
            f"/api/v3/knowledge-candidates/{arguments.candidate_id}/reject",
            json=payload,
        )
    record = response.get("candidate", {})
    return Result(
        data=response,
        lines=fields(
            {
                "candidate_id": record.get("candidate_id"),
                "status": record.get("status"),
                "reviewer": record.get("reviewer"),
                "reviewed_at": record.get("reviewed_at"),
                "promotion_event_id": response.get("event", {}).get("event_id"),
            }
        ),
    )


async def promote(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Explicitly promote an approved candidate; the server enforces V1 target scope."""
    payload = {
        "research_project_id": arguments.research_project_id,
        "expected_revision": arguments.expected_revision,
        "supersedes_context_id": arguments.supersedes_context_id,
    }
    async with api_client(settings) as client:
        response = await request(
            client,
            "POST",
            f"/api/v3/knowledge-candidates/{arguments.candidate_id}/promote",
            json=payload,
        )
    record = response.get("candidate", {})
    target = response.get("target", {})
    return Result(
        data=response,
        lines=fields(
            {
                "candidate_id": record.get("candidate_id"),
                "status": record.get("status"),
                "target_type": target.get("target_type"),
                "target_id": target.get("target_id"),
                "promotion_event_id": response.get("event", {}).get("event_id"),
                "skills_modified": False,
            }
        ),
    )


def add_arguments(subparsers: SubParsers) -> None:
    """Register only Phase 9 candidate list and explicit review operations."""
    group = subparsers.add_parser(
        "candidate",
        help="review research-derived knowledge proposals",
        description="Review research-derived proposals and explicitly promote approved V1 targets.",
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)

    listing = commands.add_parser("list", help="list authorized knowledge candidates")
    listing.add_argument("--research-project-id", required=True)
    listing.add_argument("--status", choices=_STATES)
    listing.add_argument("--target-project-id")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--cursor")
    listing.set_defaults(handler=list_candidates, needs_platform=True)

    approving = commands.add_parser(
        "approve", help="approve one pending candidate without promotion"
    )
    approving.add_argument("--research-project-id", required=True)
    approving.add_argument("--candidate-id", required=True)
    approving.add_argument("--expected-revision", type=int, required=True)
    approving.add_argument("--reason")
    approving.set_defaults(handler=approve, needs_platform=True)

    rejecting = commands.add_parser("reject", help="reject or return one candidate for changes")
    rejecting.add_argument("--research-project-id", required=True)
    rejecting.add_argument("--candidate-id", required=True)
    rejecting.add_argument("--expected-revision", type=int, required=True)
    rejecting.add_argument("--reason")
    rejecting.add_argument("--needs-changes", action="store_true")
    rejecting.set_defaults(handler=reject, needs_platform=True)

    promoting = commands.add_parser(
        "promote", help="promote one approved candidate into its supported V1 target"
    )
    promoting.add_argument("--research-project-id", required=True)
    promoting.add_argument("--candidate-id", required=True)
    promoting.add_argument("--expected-revision", type=int, required=True)
    promoting.add_argument(
        "--supersedes-context-id",
        help="explicit active V1 context id to supersede after project-context promotion",
    )
    promoting.set_defaults(handler=promote, needs_platform=True)
