"""One identifier that follows a call through everything it touches.

An operator reading a log has one question first: *which call was that?* A
request identifier is created at the edge, carried in a context variable so no
function has to be handed one, attached to every line the call produces, and
returned in a header so the caller can quote it. When the MCP server makes the
call it sends its own identifier along, so one tool call and the API request it
became share an identifier across two processes.

The other fields are what the call turns out to be about — the operation, the
project, the query, how much came back — filled in as they become known and
logged once at the end. They are identifiers and counts, never content: a query
identifier says which search, never what was asked.

A caller-supplied identifier is accepted only if it is short and alphanumeric.
Correlation is worth carrying across a boundary, but an identifier ends up in
every log line of the call, so nothing that could not safely be one is taken.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Final
from uuid import uuid4

# Long enough for a UUID in any spelling, and for a trace identifier from
# another system. Short enough that a log line stays readable.
MAX_ID_LENGTH: Final = 64
_ACCEPTABLE: Final = re.compile(rf"^[A-Za-z0-9_.:-]{{1,{MAX_ID_LENGTH}}}$")
# The header a caller passes an identifier in, and the one every answer carries.
HEADER: Final = "X-Request-ID"


@dataclass(slots=True)
class Correlation:
    """What one call is, as much of it as is known so far.

    Mutable on purpose: the identifier exists before anything is known about
    the call, and the rest is filled in by whoever learns it.
    """

    request_id: str
    operation: str | None = None
    project_id: str | None = None
    query_id: str | None = None
    result_count: int | None = None

    def fields(self) -> dict[str, object]:
        """Return the correlation fields that have a value, for a log `extra`."""
        found: dict[str, object] = {"request_id": self.request_id}
        if self.operation is not None:
            found["operation"] = self.operation
        if self.project_id is not None:
            found["project_id"] = self.project_id
        if self.query_id is not None:
            found["query_id"] = self.query_id
        if self.result_count is not None:
            found["result_count"] = self.result_count
        return found


_current: ContextVar[Correlation | None] = ContextVar("correlation", default=None)


def new_id() -> str:
    """Return a fresh request identifier."""
    return uuid4().hex


def accepted(value: str | None) -> str | None:
    """Return a caller-supplied identifier, or nothing if it cannot be carried.

    Refusing is the default. This value is repeated on every log line the call
    produces, so it is taken only when it is short and made of characters that
    cannot be mistaken for structure by anything that later reads those lines.
    """
    if value is None:
        return None
    candidate = value.strip()
    return candidate if _ACCEPTABLE.match(candidate) else None


def begin(request_id: str | None = None) -> Token[Correlation | None]:
    """Start a correlated call, adopting an acceptable identifier or minting one."""
    return _current.set(Correlation(request_id=accepted(request_id) or new_id()))


def end(token: Token[Correlation | None]) -> None:
    """End the call `begin` started."""
    _current.reset(token)


@contextmanager
def correlated(request_id: str | None = None) -> Iterator[Correlation]:
    """Run a block as one correlated call."""
    token = begin(request_id)
    try:
        yield current_or_fail()
    finally:
        end(token)


def current() -> Correlation | None:
    """Return the call in progress, if this code is running inside one."""
    return _current.get()


def current_or_fail() -> Correlation:
    """Return the call in progress, which `correlated` guarantees exists."""
    correlation = _current.get()
    if correlation is None:  # pragma: no cover - `correlated` always sets one
        raise RuntimeError("no correlated call is in progress")
    return correlation


def request_id() -> str | None:
    """Return the identifier of the call in progress, if there is one."""
    correlation = _current.get()
    return None if correlation is None else correlation.request_id


def annotate(
    *,
    operation: str | None = None,
    project_id: str | None = None,
    query_id: str | None = None,
    result_count: int | None = None,
) -> None:
    """Record what the call in progress turned out to be about.

    Outside a correlated call this does nothing, so a service can annotate
    without knowing whether it was reached through the API, the CLI, or a test.
    """
    correlation = _current.get()
    if correlation is None:
        return
    if operation is not None:
        correlation.operation = operation
    if project_id is not None:
        correlation.project_id = project_id
    if query_id is not None:
        correlation.query_id = query_id
    if result_count is not None:
        correlation.result_count = result_count


def fields() -> dict[str, object]:
    """Return the correlation fields of the call in progress, if any."""
    correlation = _current.get()
    return {} if correlation is None else correlation.fields()
