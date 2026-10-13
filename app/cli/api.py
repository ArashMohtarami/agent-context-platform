"""Reaching the Context API from the command line.

The commands here ask the API rather than the database, because everything they
do is something the API already does, and a second implementation of retrieval
or project rules would be a second thing to keep correct.

The HTTP client is the one the MCP server uses. Both are API clients with the
same needs — documented routes only, a bearer token that is never logged, safe
failures — and two copies of that would be the duplication worth avoiding, so
this module adapts its failures to exit codes rather than restating them.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from app.cli.config import CliSettings
from app.cli.errors import CliError, UnavailableError
from app.mcp.client import ContextApiClient, Method
from app.mcp.errors import ToolError as ApiError
from app.mcp.errors import UnavailableError as ApiUnavailableError
from app.mcp.errors import UpstreamError

# The API answers with these when a record is absent or a write conflicts.
# Neither means the platform is broken, so neither should look like it.
_OPERATION_FAILURES = frozenset({400, 404, 409, 422})


@asynccontextmanager
async def api_client(settings: CliSettings) -> AsyncIterator[ContextApiClient]:
    """Open a client against the configured Context API."""
    try:
        token = settings.api_token_value()
    except ValueError as error:
        raise CliError(str(error)) from error
    client = ContextApiClient(
        settings.api_base_url,
        token=token,
        timeout_seconds=settings.request_timeout_seconds,
    )
    try:
        yield client
    finally:
        await client.close()


async def request(
    client: ContextApiClient,
    method: Method,
    path: str,
    *,
    params: Mapping[str, object] | None = None,
    json: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Issue one API request, translating failure into an exit code.

    A refused operation and an absent platform are different problems for
    whoever is running this: one means the command was wrong, the other means
    nothing is listening.
    """
    try:
        return await client.request(method, path, params=params, json=json)
    except ApiUnavailableError as error:
        raise UnavailableError(
            str(error),
            hint="check that the platform is running: contextctl health",
        ) from error
    except UpstreamError as error:
        if error.status_code in _OPERATION_FAILURES:
            raise CliError(str(error)) from error
        raise UnavailableError(str(error)) from error
    except ApiError as error:
        raise CliError(str(error)) from error


async def probe(client: ContextApiClient, path: str) -> tuple[int, dict[str, Any]]:
    """Read a health endpoint, treating "not ready" as an answer to report."""
    try:
        return await client.probe(path)
    except ApiUnavailableError as error:
        raise UnavailableError(
            str(error),
            hint=f"is the API listening at {client.base_url}?",
        ) from error
