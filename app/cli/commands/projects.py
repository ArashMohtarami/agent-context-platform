"""Project commands: the isolation boundaries every other rule is written against."""

import argparse

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result, fields, table

_COLUMNS = ("PROJECT", "ENVIRONMENT", "NAME", "REPOSITORY")


async def create(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Create one project boundary."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "POST",
            "/api/v1/projects",
            json={
                "project_id": arguments.project_id,
                "name": arguments.name,
                "description": arguments.description,
                "repository": arguments.repository,
                "environment": arguments.environment,
                "tags": arguments.tag,
            },
        )
    return Result(
        data=body,
        lines=fields(
            {
                "project_id": body.get("project_id"),
                "name": body.get("name"),
                "environment": body.get("environment"),
                "repository": body.get("repository"),
                "tags": body.get("tags"),
            }
        ),
    )


async def list_projects(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """List the projects the platform holds knowledge for."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "GET",
            "/api/v1/projects",
            params={"limit": arguments.limit, "cursor": arguments.cursor},
        )
    items = body.get("items", [])
    lines = table(
        _COLUMNS,
        [
            (
                item.get("project_id"),
                item.get("environment"),
                item.get("name"),
                item.get("repository"),
            )
            for item in items
        ],
    )
    if body.get("next_cursor"):
        lines.append(f"\nmore: --cursor {body['next_cursor']}")
    return Result(data=body, lines=lines)


def add_arguments(subparsers: SubParsers) -> None:
    """Register the project commands."""
    group = subparsers.add_parser(
        "projects",
        help="manage project boundaries",
        description="Manage the project boundaries context and memory belong to.",
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)

    creating = commands.add_parser(
        "create",
        help="create one project",
        description=(
            "Create one project boundary. The identifier is permanent: it derives the document "
            "key, so it cannot be taken twice and nothing can move between projects."
        ),
        epilog=(
            "example: contextctl projects create --project-id payment-service "
            '--name "Payment Service" --description "Card and wallet payments" '
            "--repository git@example.invalid:sampleco/payment-service.git "
            "--environment production --tag payments"
        ),
    )
    creating.add_argument("--project-id", required=True, help="permanent identifier")
    creating.add_argument("--name", required=True, help="human-readable name")
    creating.add_argument("--description", required=True, help="what this system is")
    creating.add_argument("--repository", required=True, help="where its code lives")
    creating.add_argument("--environment", required=True, help="production, staging, …")
    creating.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="TAG",
        help="repeatable",
    )
    creating.set_defaults(handler=create, needs_platform=True)

    listing = commands.add_parser(
        "list",
        help="list projects",
        description="List the projects the platform holds knowledge for.",
        epilog="example: contextctl projects list --limit 50",
    )
    listing.add_argument("--limit", type=int, default=20, help="page size (default: 20)")
    listing.add_argument("--cursor", help="continue a previous page")
    listing.set_defaults(handler=list_projects, needs_platform=True)
