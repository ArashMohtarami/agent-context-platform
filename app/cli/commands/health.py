"""The health command: whether the platform can answer at all.

This is the command a deployment script gates on, so it exits non-zero when the
platform is not ready — a readiness probe that always succeeds would be worse
than none.
"""

import argparse
from typing import Any

from app.cli.api import api_client, probe
from app.cli.commands import SubParsers
from app.cli.config import CliSettings
from app.cli.errors import UnavailableError
from app.cli.output import Result, table

# Each part of readiness, named as an operator would ask about it. Readiness
# computes whether each is ready; this reads that answer rather than
# re-deriving it from index names and dimensions.
_COMPONENTS = (
    ("database", "MongoDB"),
    ("replica_set", "Replica set"),
    ("mongot", "Search coordinator"),
    ("embedding", "Embedding model"),
    ("vector_search", "Semantic search"),
    ("lexical_search", "Lexical search"),
    ("context_search", "Project context search"),
    ("memory_search", "Memory search"),
)


async def health(arguments: argparse.Namespace, settings: CliSettings) -> Result:
    """Report platform readiness, or liveness alone."""
    path = "/health/live" if arguments.live else "/health/ready"
    async with api_client(settings) as client:
        status_code, body = await probe(client, path)
    if arguments.live:
        alive = status_code == 200
        data: dict[str, Any] = {"alive": alive, "status": body.get("status")}
        if not alive:
            raise UnavailableError("the API process is not alive")
        return Result(data=data, lines=[f"alive: {body.get('status', 'yes')}"])

    ready = status_code == 200 and body.get("status") == "ready"
    components = {name: _component(body.get(name)) for name, _ in _COMPONENTS}
    failing = sorted(name for name, healthy in components.items() if healthy is False)
    data = {"ready": ready, "components": components, "failing": failing}
    embedding = body.get("embedding")
    if isinstance(embedding, dict):
        data["embedding_model"] = embedding.get("model")
        data["embedding_dimensions"] = embedding.get("dimensions")
    lines = table(
        ("COMPONENT", "STATE"),
        [(label, _state(components[name])) for name, label in _COMPONENTS],
    )
    lines.append(f"\nready: {'yes' if ready else 'no'}")
    if data.get("embedding_model"):
        lines.append(f"model: {data['embedding_model']} ({data['embedding_dimensions']}d)")
    if not ready:
        raise UnavailableError(
            "the platform is not ready" + (f": {', '.join(failing)} failing" if failing else ""),
            hint="retrieval may return nothing while a capability is failing",
        )
    return Result(data=data, lines=lines)


def _component(value: object) -> bool | None:
    """Reduce one readiness entry to healthy, unhealthy, or unreported."""
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        ready = value.get("ready")
        return ready if isinstance(ready, bool) else None
    return None


def _state(healthy: bool | None) -> str:
    """Name one component's state for a terminal."""
    if healthy is None:
        return "unknown"
    return "ok" if healthy else "FAILING"


def add_arguments(subparsers: SubParsers) -> None:
    """Register the health command."""
    command = subparsers.add_parser(
        "health",
        help="report whether the platform can answer",
        description=(
            "Report platform readiness: MongoDB, the replica set, the search coordinator, the "
            "embedding model, and each search index. Exits 3 when the platform is not ready, so "
            "a deployment script can gate on it."
        ),
        epilog=(
            "examples:\n"
            "  contextctl health\n"
            "  contextctl health --live\n"
            "  contextctl --json health | jq .failing"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    command.add_argument(
        "--live",
        action="store_true",
        help="check only that the API process is alive, not that it is ready",
    )
    command.set_defaults(handler=health, needs_platform=True)
