"""Operator commands for V4's explicit project bindings.

They remain API clients: the API performs canonicalization, authorization, and
revision checks, so this CLI never creates a second project-resolution policy.
"""

import argparse

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result, fields, table

_BINDING_TYPES = ("git_remote", "repository_url", "repository_name", "workspace_alias")
_COLUMNS = ("BINDING", "PROJECT", "TYPE", "VALUE", "REVISION")


async def add_binding(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Create one deterministic project binding through the authorized API."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "POST",
            "/api/v4/project-bindings",
            json={
                "project_id": arguments.project_id,
                "binding_type": arguments.binding_type,
                "value": arguments.value,
            },
        )
    return Result(data=body, lines=fields(body))


async def list_bindings(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """List authorized project bindings with cursor pagination."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "GET",
            "/api/v4/project-bindings",
            params={
                "project_id": arguments.project_id,
                "binding_type": arguments.binding_type,
                "limit": arguments.limit,
                "cursor": arguments.cursor,
            },
        )
    items = body.get("items", [])
    rows = [
        (
            item.get("binding_id"),
            item.get("project_id"),
            item.get("binding_type"),
            item.get("value"),
            item.get("revision"),
        )
        for item in items
        if isinstance(item, dict)
    ]
    lines = table(_COLUMNS, rows)
    if body.get("next_cursor"):
        lines.append(f"\nmore: --cursor {body['next_cursor']}")
    return Result(data=body, lines=lines)


async def delete_binding(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Delete only the binding revision an operator deliberately reviewed."""
    async with api_client(settings) as client:
        await request(
            client,
            "DELETE",
            f"/api/v4/project-bindings/{arguments.binding_id}",
            params={"expected_revision": arguments.revision},
        )
    data = {"binding_id": arguments.binding_id, "deleted": True}
    return Result(data=data, lines=fields(data))


def add_arguments(groups: SubParsers) -> None:
    """Register singular `project binding` commands without altering V1 `projects`."""
    project = groups.add_parser("project", help="manage one project's V4 bindings")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    bindings = project_commands.add_parser("binding", help="manage explicit project bindings")
    commands = bindings.add_subparsers(dest="binding_command", required=True)

    adding = commands.add_parser("add", help="bind one exact identifier to a project")
    adding.add_argument("--project-id", required=True)
    adding.add_argument("--type", dest="binding_type", required=True, choices=_BINDING_TYPES)
    adding.add_argument("--value", required=True)
    adding.set_defaults(handler=add_binding, needs_platform=True)

    listing = commands.add_parser("list", help="list authorized project bindings")
    listing.add_argument("--project-id")
    listing.add_argument("--type", dest="binding_type", choices=_BINDING_TYPES)
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--cursor")
    listing.set_defaults(handler=list_bindings, needs_platform=True)

    deleting = commands.add_parser("delete", help="delete one reviewed binding")
    deleting.add_argument("binding_id")
    deleting.add_argument("--revision", type=int, required=True)
    deleting.set_defaults(handler=delete_binding, needs_platform=True)
