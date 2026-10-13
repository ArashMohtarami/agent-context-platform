"""Creator-profile commands backed exclusively by the V2 API."""

import argparse
from typing import Any

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.errors import UsageError
from app.cli.output import Result, fields, table

_PILLAR_COLUMNS = ("PILLAR", "STATUS", "PRIORITY", "NAME", "TAGS")


async def show(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Read one creator profile through the same API used by MCP."""
    async with api_client(settings) as client:
        body = await request(client, "GET", f"/api/v2/creator-profiles/{arguments.profile_id}")
    return Result(
        data=body,
        lines=fields(
            {
                "profile_id": body.get("profile_id"),
                "name": body.get("name"),
                "status": body.get("status"),
                "audiences": body.get("audiences"),
                "domains": body.get("domains"),
                "voice": body.get("voice"),
                "tone_preferences": body.get("tone_preferences"),
                "topics_of_interest": body.get("topics_of_interest"),
                "topics_to_avoid": body.get("topics_to_avoid"),
                "content_objectives": body.get("content_objectives"),
                "revision": body.get("revision"),
                "updated_at": body.get("updated_at"),
            }
        ),
    )


async def update(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Forward one revision-guarded profile preference update to the API."""
    payload: dict[str, Any] = {"expected_revision": arguments.expected_revision}
    scalar_fields = ("name", "status")
    list_fields = (
        "audiences",
        "domains",
        "writing_preferences",
        "formatting_preferences",
        "terminology_preferences",
        "language_preferences",
        "tone_preferences",
        "topics_of_interest",
        "topics_to_avoid",
        "content_objectives",
        "tags",
    )
    for field in scalar_fields:
        value = getattr(arguments, field)
        if value is not None:
            payload[field] = value
    for field in list_fields:
        value = getattr(arguments, field)
        if value is not None:
            payload[field] = value
    if arguments.voice_tone is not None or arguments.voice_avoid is not None:
        if arguments.voice_tone is None or arguments.voice_avoid is None:
            raise UsageError("--voice-tone and --voice-avoid must be used together")
        payload["voice"] = {"tone": arguments.voice_tone, "avoid": arguments.voice_avoid}
    if len(payload) == 1:
        raise UsageError("provide at least one creator profile field to update")
    async with api_client(settings) as client:
        body = await request(
            client,
            "PATCH",
            f"/api/v2/creator-profiles/{arguments.profile_id}",
            json=payload,
        )
    return Result(
        data=body,
        lines=fields(
            {
                "profile_id": body.get("profile_id"),
                "name": body.get("name"),
                "status": body.get("status"),
                "revision": body.get("revision"),
                "updated_at": body.get("updated_at"),
            }
        ),
    )


async def pillars(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """List content pillars from exactly one profile boundary."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "GET",
            "/api/v2/content-pillars",
            params={
                "profile_id": arguments.profile_id,
                "limit": arguments.limit,
                "cursor": arguments.cursor,
            },
        )
    items = body.get("items", [])
    lines = table(
        _PILLAR_COLUMNS,
        [
            (
                item.get("pillar_id"),
                item.get("status"),
                item.get("priority"),
                item.get("name"),
                item.get("tags"),
            )
            for item in items
        ],
    )
    if body.get("next_cursor"):
        lines.append(f"\nmore: --cursor {body['next_cursor']}")
    return Result(data=body, lines=lines)


def add_arguments(subparsers: SubParsers) -> None:
    """Register V2 Phase 1 creator commands."""
    group = subparsers.add_parser(
        "creator",
        help="read and update creator preference data",
        description="Creator profiles are preference data, never hidden system instructions.",
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)

    showing = commands.add_parser("show", help="show one creator profile")
    showing.add_argument("--profile-id", required=True, help="creator profile identifier")
    showing.set_defaults(handler=show, needs_platform=True)

    updating = commands.add_parser(
        "update",
        help="revision-guardedly update creator preferences",
        description=(
            "Update preference data through the V2 API. Supply the revision returned by `creator "
            "show`; a stale update is refused rather than overwriting another change."
        ),
    )
    updating.add_argument("--profile-id", required=True, help="creator profile identifier")
    updating.add_argument("--expected-revision", required=True, type=int, help="revision last read")
    updating.add_argument("--name", help="display name")
    updating.add_argument("--status", choices=["active", "inactive", "archived"])
    for option, field, help_text in (
        ("--audience", "audiences", "repeatable target audience"),
        ("--domain", "domains", "repeatable technical domain"),
        ("--writing-preference", "writing_preferences", "repeatable writing preference"),
        ("--formatting-preference", "formatting_preferences", "repeatable formatting preference"),
        (
            "--terminology-preference",
            "terminology_preferences",
            "repeatable terminology preference",
        ),
        ("--language-preference", "language_preferences", "repeatable language preference"),
        ("--tone-preference", "tone_preferences", "repeatable tone preference"),
        ("--topic-of-interest", "topics_of_interest", "repeatable topic"),
        ("--topic-to-avoid", "topics_to_avoid", "repeatable excluded topic"),
        ("--content-objective", "content_objectives", "repeatable objective"),
        ("--tag", "tags", "repeatable tag"),
        ("--voice-tone", "voice_tone", "repeatable structured voice tone"),
        ("--voice-avoid", "voice_avoid", "repeatable structured voice avoidance"),
    ):
        updating.add_argument(option, dest=field, action="append", default=None, help=help_text)
    updating.set_defaults(handler=update, needs_platform=True)

    listing = commands.add_parser("pillars", help="list one profile's content pillars")
    listing.add_argument("--profile-id", required=True, help="creator profile identifier")
    listing.add_argument("--limit", type=int, default=20, help="page size (default: 20)")
    listing.add_argument("--cursor", help="continue a previous page")
    listing.set_defaults(handler=pillars, needs_platform=True)
