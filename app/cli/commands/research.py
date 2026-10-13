"""V3 Phase 1 research-project commands backed only by the REST API."""

import argparse

from app.cli.api import api_client, request
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.output import Result, fields, table

_COLUMNS = ("RESEARCH PROJECT", "STATUS", "TYPE", "NAME", "RELATED PROJECTS")
_TYPES = (
    "technical_research",
    "architecture_evaluation",
    "technology_comparison",
    "incident_investigation",
    "content_research",
    "learning",
    "documentation_research",
    "market_research",
    "repository_analysis",
)


async def create(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Create research metadata without performing acquisition or retrieval."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "POST",
            "/api/v3/research/projects",
            json={
                "research_project_id": arguments.research_project_id,
                "name": arguments.name,
                "question": arguments.question,
                "type": arguments.type,
                "related_project_ids": arguments.related_project_id,
                "status": arguments.status,
            },
        )
    return Result(
        data=body,
        lines=fields(
            {
                "research_project_id": body.get("research_project_id"),
                "name": body.get("name"),
                "type": body.get("type"),
                "status": body.get("status"),
                "related_project_ids": body.get("related_project_ids"),
            }
        ),
    )


async def list_projects(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """List the research metadata visible to this API credential."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "GET",
            "/api/v3/research/projects",
            params={
                "status": arguments.status,
                "limit": arguments.limit,
                "cursor": arguments.cursor,
            },
        )
    items = body.get("items", [])
    lines = table(
        _COLUMNS,
        [
            (
                item.get("research_project_id"),
                item.get("status"),
                item.get("type"),
                item.get("name"),
                item.get("related_project_ids"),
            )
            for item in items
        ],
    )
    if body.get("next_cursor"):
        lines.append(f"\nmore: --cursor {body['next_cursor']}")
    return Result(data=body, lines=lines)


async def show(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Read one research-project record through the API's authorization boundary."""
    async with api_client(settings) as client:
        body = await request(
            client,
            "GET",
            f"/api/v3/research/projects/{arguments.research_project_id}",
        )
    return Result(
        data=body,
        lines=fields(
            {
                "research_project_id": body.get("research_project_id"),
                "name": body.get("name"),
                "question": body.get("question"),
                "type": body.get("type"),
                "status": body.get("status"),
                "related_project_ids": body.get("related_project_ids"),
                "authorization": body.get("authorization"),
                "revision": body.get("revision"),
                "updated_at": body.get("updated_at"),
            }
        ),
    )


async def search(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Search authorized V3 evidence and linked V1 retrieval sources through the REST API."""
    payload = {
        "research_project_id": arguments.research_project_id,
        "query": arguments.query,
        "retrieval_profile": arguments.retrieval_profile,
        "method": arguments.method,
        "phrase": arguments.phrase,
        "source_classes": arguments.source_class,
        "source_types": arguments.source_type,
        "publisher": arguments.publisher,
        "domain": arguments.domain,
        "published_after": arguments.published_after,
        "published_before": arguments.published_before,
        "retrieved_after": arguments.retrieved_after,
        "as_of": arguments.as_of,
        "related_project_id": arguments.related_project_id,
        "token_budget": arguments.token_budget,
        "limit": arguments.limit,
        "cursor": arguments.cursor,
    }
    async with api_client(settings) as client:
        body = await request(client, "POST", "/api/v3/research/search", json=payload)
    lines = table(
        ("KIND", "SOURCE", "SCORE", "TITLE", "SOURCE ID"),
        [
            (
                item.get("kind"),
                item.get("source"),
                item.get("retrieval_score"),
                item.get("title"),
                item.get("source_id"),
            )
            for item in body.get("items", [])
        ],
    )
    if body.get("next_cursor"):
        lines.append(f"\nmore: --cursor {body['next_cursor']}")
    return Result(data=body, lines=lines)


async def brief(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Build structured brief material; the API retains provenance and no prose conclusion."""
    payload = {
        "research_project_id": arguments.research_project_id,
        "brief_id": arguments.brief_id,
        "scope": arguments.scope,
        "additional_unknowns": arguments.additional_unknown,
        "next_questions": arguments.next_question,
        "status": arguments.status,
    }
    async with api_client(settings) as client:
        body = await request(client, "POST", "/api/v3/research/briefs", json=payload)
    return Result(
        data=body,
        lines=fields(
            {
                "brief_id": body.get("brief_id"),
                "research_project_id": body.get("research_project_id"),
                "question": body.get("question"),
                "key_findings": body.get("key_findings"),
                "claim_refs": body.get("claim_refs"),
                "citation_refs": body.get("citation_refs"),
                "contradictions": body.get("contradictions"),
                "unknowns": body.get("unknowns"),
                "sources": body.get("sources"),
                "status": body.get("status"),
            }
        ),
    )


def add_arguments(subparsers: SubParsers) -> None:
    """Register the narrow V3 Phase 1 research-project CLI group."""
    group = subparsers.add_parser(
        "research",
        help="manage research projects and search authorized evidence",
        description="Create and inspect research questions; this command never ingests sources.",
    )
    commands = group.add_subparsers(dest="command", metavar="COMMAND", required=True)

    creating = commands.add_parser("create", help="create one research project")
    creating.add_argument("--research-project-id", required=True, help="stable research identifier")
    creating.add_argument("--name", required=True, help="human-readable research name")
    creating.add_argument(
        "--question", required=True, help="question the research will investigate"
    )
    creating.add_argument("--type", required=True, choices=_TYPES, help="research project type")
    creating.add_argument(
        "--related-project-id",
        action="append",
        default=[],
        metavar="PROJECT_ID",
        help="repeatable authorized V1 project link",
    )
    creating.add_argument("--status", choices=("active", "archived"), default="active")
    creating.set_defaults(handler=create, needs_platform=True)

    listing = commands.add_parser("list", help="list visible research projects")
    listing.add_argument("--status", choices=("active", "archived"), help="lifecycle filter")
    listing.add_argument("--limit", type=int, default=20, help="page size (default: 20)")
    listing.add_argument("--cursor", help="continue a previous page")
    listing.set_defaults(handler=list_projects, needs_platform=True)

    showing = commands.add_parser("show", help="show one research project")
    showing.add_argument("--research-project-id", required=True, help="research project identifier")
    showing.set_defaults(handler=show, needs_platform=True)

    searching = commands.add_parser("search", help="search authorized research evidence")
    searching.add_argument(
        "--research-project-id", required=True, help="research project identifier"
    )
    searching.add_argument("--query", required=True, help="research query")
    searching.add_argument(
        "--retrieval-profile",
        choices=(
            "research_general",
            "research_primary_sources",
            "research_recent",
            "research_content",
            "research_architecture",
            "research_code",
        ),
        default="research_general",
    )
    searching.add_argument("--method", choices=("vector", "lexical", "hybrid"), default="hybrid")
    searching.add_argument("--phrase", action="store_true", help="prefer exact lexical phrases")
    searching.add_argument(
        "--source-class",
        action="append",
        default=[],
        choices=(
            "primary",
            "official_documentation",
            "standard",
            "source_code",
            "academic",
            "secondary",
            "community",
            "user_provided",
            "generated",
            "unknown",
        ),
    )
    searching.add_argument(
        "--source-type",
        action="append",
        default=[],
        choices=(
            "manual",
            "url",
            "file",
            "markdown",
            "pdf",
            "git",
            "github",
            "documentation",
            "rss",
        ),
    )
    searching.add_argument("--publisher")
    searching.add_argument("--domain", help="hostname only, for example docs.python.org")
    searching.add_argument("--published-after", help="ISO-8601 timestamp")
    searching.add_argument("--published-before", help="ISO-8601 timestamp")
    searching.add_argument("--retrieved-after", help="ISO-8601 timestamp")
    searching.add_argument("--as-of", help="ISO-8601 evidence cutoff")
    searching.add_argument("--related-project-id", help="restrict linked V1 project context/memory")
    searching.add_argument("--token-budget", type=int, help="bounded context packet token budget")
    searching.add_argument("--limit", type=int, default=10, help="maximum results (default: 10)")
    searching.add_argument("--cursor", help="continue a previous result page")
    searching.set_defaults(handler=search, needs_platform=True)

    briefing = commands.add_parser(
        "brief",
        help="build structured research material with preserved citations",
        description=(
            "Builds claims, citations, contradictions, unknowns, and source references. "
            "It does not generate prose, claim completeness, or promote knowledge."
        ),
    )
    briefing.add_argument("--research-project-id", required=True)
    briefing.add_argument("--brief-id", required=True)
    briefing.add_argument("--scope", required=True)
    briefing.add_argument(
        "--additional-unknown",
        action="append",
        default=[],
        help="repeatable explicit uncertainty to retain in the brief",
    )
    briefing.add_argument(
        "--next-question",
        action="append",
        default=[],
        help="repeatable follow-up research question",
    )
    briefing.add_argument("--status", choices=("draft", "ready", "archived"), default="draft")
    briefing.set_defaults(handler=brief, needs_platform=True)
