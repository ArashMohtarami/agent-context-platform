"""Creator-content discovery and brief commands backed only by the V2 API."""

import argparse
from pathlib import Path
from typing import Any

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.errors import CliError, UsageError
from app.cli.output import Result, fields, table

_IDEA_COLUMNS = ("TOPIC", "PILLAR", "PROJECT", "PLATFORMS", "SOURCES", "UPDATED")


async def ideas(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Discover deterministic, evidence-backed topic labels without saving them."""
    time_range = {
        key: value
        for key, value in (("start_at", arguments.since), ("end_at", arguments.until))
        if value is not None
    }
    payload: dict[str, Any] = {
        "profile_id": arguments.profile_id,
        "project_id": arguments.project_id,
        "pillar_id": arguments.pillar_id,
        "platform": arguments.platform,
        "limit": arguments.limit,
    }
    if time_range:
        payload["time_range"] = time_range
    async with api_client(settings) as client:
        body = await request(client, "POST", "/api/v2/content-ideas/discover", json=payload)
    candidates = body.get("candidates", [])
    lines = table(
        _IDEA_COLUMNS,
        [
            (
                item.get("topic"),
                item.get("related_pillar_id"),
                item.get("related_project_id"),
                item.get("suggested_platforms"),
                len(item.get("source_refs", [])),
                (item.get("freshness") or {}).get("updated_at"),
            )
            for item in candidates
        ],
    )
    if not candidates:
        lines.append("no source-backed ideas found in the requested scope")
    existing = body.get("existing_unpublished_ideas", [])
    if existing:
        lines.append(
            f"\n{len(existing)} existing unpublished ideas were retained for duplicate avoidance"
        )
    return Result(data=body, lines=lines)


async def create_idea(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Save a reviewed candidate through the existing lifecycle API."""
    payload = {
        "profile_id": arguments.profile_id,
        "idea_id": arguments.idea_id,
        "title": arguments.title,
        "description": arguments.description,
        "pillar_id": arguments.pillar_id,
        "platforms": arguments.platform,
        "origin": {
            "type": arguments.origin_type,
            "project_id": arguments.project_id,
            "source_ids": arguments.origin_source_id,
        },
        "tags": arguments.tag,
        "priority": arguments.priority,
    }
    async with api_client(settings) as client:
        body = await request(client, "POST", "/api/v2/content-ideas", json=payload)
    return Result(
        data=body,
        lines=fields(
            {
                "idea_id": body.get("idea_id"),
                "title": body.get("title"),
                "status": body.get("status"),
                "pillar_id": body.get("pillar_id"),
                "platforms": body.get("platforms"),
                "source_ids": (body.get("origin") or {}).get("source_ids"),
            }
        ),
    )


async def brief(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Prepare a source-backed brief package without creating a content brief record."""
    payload = {
        "profile_id": arguments.profile_id,
        "idea_id": arguments.idea_id,
        "platform": arguments.platform,
        "project_id": arguments.project_id,
        "pillar_id": arguments.pillar_id,
    }
    async with api_client(settings) as client:
        body = await request(client, "POST", "/api/v2/content-briefs/prepare", json=payload)
    return Result(
        data=body,
        lines=fields(
            {
                "objective": body.get("objective"),
                "audience": body.get("audience"),
                "primary_message": body.get("primary_message"),
                "supporting_points": body.get("supporting_points"),
                "source_refs": body.get("source_refs"),
                "technical_skills": body.get("technical_skills"),
                "content_skill": body.get("content_skill"),
                "similar_previous_content": body.get("similar_previous_content"),
                "content_constraints": body.get("content_constraints"),
            }
        ),
    )


async def novelty(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Ask the API for advisory history overlap without creating anything."""
    payload = {
        "profile_id": arguments.profile_id,
        "title": arguments.title,
        "description": arguments.description,
        "tags": arguments.tag,
        "pillar_id": arguments.pillar_id,
        "platform": arguments.platform,
        "project_id": arguments.project_id,
        "method": arguments.method,
        "token_budget": arguments.token_budget,
        "limit": arguments.limit,
    }
    async with api_client(settings) as client:
        body = await request(client, "POST", "/api/v2/content/novelty", json=payload)
    lines = fields(
        {
            "classification (advisory)": body.get("classification"),
            "topic_overlap": body.get("topic_overlap"),
            "angle_overlap": body.get("angle_overlap"),
        }
    )
    similar = body.get("similar_content", [])
    if similar:
        lines.extend(
            [
                "",
                *table(
                    ("ID", "TITLE", "CLASSIFICATION", "TOPIC", "ANGLE"),
                    [
                        (
                            item.get("identifier"),
                            item.get("title"),
                            item.get("classification"),
                            item.get("topic_overlap"),
                            item.get("angle_overlap"),
                        )
                        for item in similar
                    ],
                ),
            ]
        )
    else:
        lines.append("no explainable overlap found in visible content history")
    return Result(data=body, lines=lines)


async def repurpose(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Request a structured target plan through the API, never a generated draft."""
    payload = {
        "profile_id": arguments.profile_id,
        "source_kind": arguments.source_kind,
        "targets": arguments.target,
        "method": arguments.method,
        "token_budget": arguments.token_budget,
        "limit": arguments.limit,
    }
    async with api_client(settings) as client:
        body = await request(
            client,
            "POST",
            f"/api/v2/content/{arguments.content_id}/repurpose",
            json=payload,
        )
    return Result(
        data=body,
        lines=fields(
            {
                "source": f"{body.get('source_kind')}:{body.get('source_id')}",
                "core_thesis": body.get("core_thesis"),
                "source_refs": body.get("source_refs"),
                "targets": [
                    {
                        "platform": target.get("platform"),
                        "content_skill": (target.get("target_content_skill") or {}).get("skill_id"),
                        "shorten": target.get("what_to_shorten"),
                        "expand": target.get("what_requires_expansion"),
                    }
                    for target in body.get("targets", [])
                ],
            }
        ),
    )


async def export(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Request a manual representation, optionally writing only to an explicit local file."""
    if arguments.mode == "file" and arguments.output is None:
        raise UsageError("--output is required when --mode file is selected")
    if arguments.mode != "file" and arguments.output is not None:
        raise UsageError("--output may only be used with --mode file")
    if arguments.mode != "file" and arguments.force:
        raise UsageError("--force may only be used with --mode file")
    payload = {"profile_id": arguments.profile_id, "mode": arguments.mode}
    async with api_client(settings) as client:
        body = await request(
            client,
            "POST",
            f"/api/v2/content/{arguments.content_id}/export",
            json=payload,
        )
    text = str(body.get("text", ""))
    if arguments.mode == "file":
        output = arguments.output
        if output is None:
            raise UsageError("--output is required when --mode file is selected")
        destination = _write_export(output, text, force=arguments.force)
        return Result(
            data={**body, "written_to": str(destination)},
            lines=fields(
                {
                    "written_to": destination,
                    "format": body.get("format"),
                    "suggested_path": body.get("suggested_path"),
                }
            ),
        )
    return Result(data=body, lines=(text,))


def _write_export(value: str, text: str, *, force: bool) -> Path:
    """Write a caller-selected export without silently overwriting a local file."""
    destination = Path(value)
    try:
        if destination.exists():
            if destination.is_dir():
                raise UsageError("--output must name a file, not a directory")
            if not force:
                raise UsageError("refusing to overwrite --output; pass --force to replace it")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    except UsageError:
        raise
    except OSError as error:
        raise CliError("could not write the requested export file") from error
    return destination


def add_arguments(subparsers: SubParsers) -> None:
    """Register Phase 5-7 content commands; platform logic stays behind the API."""
    group = subparsers.add_parser(
        "content",
        help="discover content inputs, inspect novelty, prepare plans, and export drafts",
        description=(
            "Discovery returns structured evidence. It never generates or publishes content."
        ),
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)

    discovery = commands.add_parser("ideas", help="discover source-backed content ideas")
    discovery.add_argument("--profile-id", required=True, help="creator profile identifier")
    discovery.add_argument("--project-id", help="only inspect this project and its project memory")
    discovery.add_argument("--pillar-id", help="only return evidence matching this content pillar")
    discovery.add_argument("--platform", choices=["linkedin", "medium", "github"])
    discovery.add_argument("--since", help="inclusive ISO-8601 update timestamp")
    discovery.add_argument("--until", help="inclusive ISO-8601 update timestamp")
    discovery.add_argument(
        "--limit", type=int, default=10, help="idea candidates at most (default: 10)"
    )
    discovery.set_defaults(handler=ideas, needs_platform=True)

    idea_group = commands.add_parser("idea", help="create an explicitly reviewed content idea")
    idea_commands = idea_group.add_subparsers(dest="idea_command", metavar="COMMAND", required=True)
    creating = idea_commands.add_parser("create", help="save one reviewed content idea")
    creating.add_argument("--profile-id", required=True)
    creating.add_argument("--idea-id", required=True)
    creating.add_argument("--title", required=True)
    creating.add_argument("--description", required=True)
    creating.add_argument("--pillar-id")
    creating.add_argument(
        "--platform",
        action="append",
        required=True,
        choices=["linkedin", "medium", "github"],
    )
    creating.add_argument("--origin-type", required=True, help="source type used for this idea")
    creating.add_argument(
        "--origin-source-id", action="append", required=True, help="repeatable source ID"
    )
    creating.add_argument("--project-id", help="project owning the supplied origin source IDs")
    creating.add_argument("--tag", action="append", default=[], help="repeatable topic tag")
    creating.add_argument("--priority", type=float, required=True, help="0..1")
    creating.set_defaults(handler=create_idea, needs_platform=True)

    preparing = commands.add_parser("brief", help="prepare a non-persistent evidence brief")
    preparing.add_argument("--profile-id", required=True)
    preparing.add_argument("--idea-id", required=True)
    preparing.add_argument("--platform", required=True, choices=["linkedin", "medium", "github"])
    preparing.add_argument("--project-id")
    preparing.add_argument("--pillar-id")
    preparing.set_defaults(handler=brief, needs_platform=True)

    checking = commands.add_parser("novelty", help="check advisory overlap with content history")
    checking.add_argument("--profile-id", required=True)
    checking.add_argument("--title", required=True)
    checking.add_argument("--description", required=True)
    checking.add_argument("--tag", action="append", default=[])
    checking.add_argument("--pillar-id")
    checking.add_argument("--platform", choices=["linkedin", "medium", "github"])
    checking.add_argument("--project-id")
    checking.add_argument("--method", choices=["vector", "lexical", "hybrid"], default="hybrid")
    checking.add_argument("--token-budget", type=int)
    checking.add_argument("--limit", type=int, default=10)
    checking.set_defaults(handler=novelty, needs_platform=True)

    planning = commands.add_parser("repurpose", help="prepare non-generative target-platform plans")
    planning.add_argument("--profile-id", required=True)
    planning.add_argument(
        "--content-id", required=True, help="source ID for the selected source kind"
    )
    planning.add_argument("--source-kind", choices=["content", "brief", "idea"], default="content")
    planning.add_argument(
        "--target",
        action="append",
        required=True,
        choices=["linkedin", "medium", "github"],
        help="target platform; repeat for multiple plans",
    )
    planning.add_argument("--method", choices=["vector", "lexical", "hybrid"], default="hybrid")
    planning.add_argument("--token-budget", type=int)
    planning.add_argument("--limit", type=int, default=5)
    planning.set_defaults(handler=repurpose, needs_platform=True)

    exporting = commands.add_parser("export", help="format a draft for manual local export")
    exporting.add_argument("--profile-id", required=True)
    exporting.add_argument("--content-id", required=True)
    exporting.add_argument("--mode", choices=["stdout", "file", "markdown"], default="stdout")
    exporting.add_argument("--output", help="local file path; required for --mode file")
    exporting.add_argument("--force", action="store_true", help="replace an existing --output file")
    exporting.set_defaults(handler=export, needs_platform=True)
