"""Load the payment-service example into a running platform.

The project is created through the Context API, because bringing an isolation
boundary into existence is an operator's decision. Everything stored inside it
goes through the MCP tools, because that is how an agent writes — so running
this is also a check that `save_context` and `save_memory` work end to end.

Every item carries an `external_id`, so running this twice stores one copy.

    python examples/payment-service/load.py --token-file secrets/payment-example-key
    python examples/payment-service/load.py --api-base-url http://127.0.0.1:8000 \\
        --token-file secrets/payment-example-key
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp.shared.memory import create_connected_server_and_client_session

from app.mcp.auth import build_authorization
from app.mcp.client import ContextApiClient
from app.mcp.server import build_server

KNOWLEDGE = Path(__file__).with_name("knowledge.json")
DEFAULT_API = "http://127.0.0.1:8000"


def parse_arguments() -> argparse.Namespace:
    """Read the Context API address this example should load into."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API,
        help=f"Context API address (default: {DEFAULT_API})",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=os.environ.get("CONTEXTCTL_API_TOKEN_FILE"),
        help="Bearer-token file; defaults to CONTEXTCTL_API_TOKEN_FILE",
    )
    return parser.parse_args()


def read_token(path: Path | None) -> str:
    """Load an explicitly configured credential without printing it."""
    if path is None:
        raise SystemExit("use --token-file or set CONTEXTCTL_API_TOKEN_FILE")
    try:
        token = path.read_text(encoding="utf-8").rstrip("\r\n")
    except (OSError, UnicodeError) as error:
        raise SystemExit("the configured token file could not be read") from error
    if not token or any(character.isspace() for character in token):
        raise SystemExit("the configured token file must contain one non-empty credential")
    return token


async def ensure_project(api_base_url: str, project: dict[str, Any], token: str) -> None:
    """Create the project, tolerating one that already exists."""
    async with httpx.AsyncClient(
        base_url=api_base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
        trust_env=False,
        follow_redirects=False,
    ) as api:
        response = await api.post("/api/v1/projects", json=project)
    if response.status_code == 201:
        print(f"created project {project['project_id']}")
    elif response.status_code == 409:
        print(f"project {project['project_id']} already exists")
    else:
        raise SystemExit(f"could not create the project: HTTP {response.status_code}")


def report(label: str, result: Any) -> None:
    """Print one tool call's outcome."""
    if result.isError:
        message = "\n".join(block.text for block in result.content if block.type == "text")
        raise SystemExit(f"{label} failed: {message}")
    print(f"  stored {label}")


async def load(api_base_url: str, knowledge: dict[str, Any], token: str) -> None:
    """Store every context item and memory through the MCP tools."""
    client = ContextApiClient(api_base_url, token=token)
    server = build_server(client, build_authorization())
    try:
        async with create_connected_server_and_client_session(server) as session:
            project_id = knowledge["project"]["project_id"]
            print(f"storing {len(knowledge['context'])} context items through MCP")
            for item in knowledge["context"]:
                report(
                    item["title"],
                    await session.call_tool(
                        "save_context",
                        {"project_id": project_id, "source": "example", **item},
                    ),
                )
            print(f"storing {len(knowledge['memory'])} memories through MCP")
            for item in knowledge["memory"]:
                report(
                    item["summary"],
                    await session.call_tool(
                        "save_memory",
                        {"source": "example", **item},
                    ),
                )
    finally:
        await client.close()


async def main() -> None:
    """Create the project and fill it with the example knowledge."""
    arguments = parse_arguments()
    token = read_token(arguments.token_file)
    knowledge = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    await ensure_project(arguments.api_base_url, knowledge["project"], token)
    await load(arguments.api_base_url, knowledge, token)
    print(
        "\nLoaded. Ask Codex something like:\n"
        '  "Implement payment refund support in payment-service."\n'
        "It should call search_knowledge with project_id=payment-service.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    asyncio.run(main())
