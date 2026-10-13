"""The metrics this platform reports, named once.

Every number the platform publishes is declared here and recorded through one
of the functions below, so the catalogue and the vocabulary are the same thing:
there is no metric that exists only where it is incremented, and no label whose
possible values are discoverable only by reading a call site.

The labels are deliberately narrow. A route template, a retrieval method, a
collection, a tool name, an outcome — all of them bounded sets this codebase
writes. What is *not* here is as deliberate: no project identifier, no query, no
document identifier, no key. Metrics are kept for a long time and read by more
people than a log, so the rule is that a series can say how much and how long,
never about whom or about what.

Both processes record into `REGISTRY`. The API renders it at `/metrics`. The
MCP server and `contextctl` serve no port and are often short-lived, so their
counters reach an operator as the structured log events emitted alongside each
recording — the same numbers, carried by the stream those processes already
have.
"""

from time import monotonic
from typing import Final

from app import __version__
from app.observability.metrics import MetricSpec, MetricsRegistry

# HTTP. The route is the template rather than the path, so one series covers
# every project rather than one series appearing per project identifier.
HTTP_REQUESTS: Final = "acp_http_requests_total"
HTTP_DURATION: Final = "acp_http_request_duration_seconds"

# MongoDB, measured by the driver itself through a command listener, so every
# query is covered without a repository having to remember to time one.
MONGODB_DURATION: Final = "acp_mongodb_command_duration_seconds"
MONGODB_FAILURES: Final = "acp_mongodb_command_failures_total"

# Embedding, which is the slowest thing in a cold search and the one most worth
# watching separately from the database it is stored in.
EMBEDDING_DURATION: Final = "acp_embedding_duration_seconds"
EMBEDDING_FAILURES: Final = "acp_embedding_failures_total"

# Search, split by method and collection: a vector index and a lexical index
# fail independently, and knowing which one is failing is the whole point.
SEARCH_DURATION: Final = "acp_search_duration_seconds"
SEARCH_FAILURES: Final = "acp_search_failures_total"

# Retrieval end to end — everything a unified search does, including the
# ranking and budgeting no single index accounts for.
RETRIEVAL_DURATION: Final = "acp_retrieval_duration_seconds"
RETRIEVAL_RESULTS: Final = "acp_retrieval_results"

# MCP, recorded in the server process. An outcome of `ok`, `refused`, or
# `failed` distinguishes a tool that worked from one a caller was not allowed
# to run from one that broke, which are three different problems.
MCP_CALLS: Final = "acp_mcp_calls_total"
MCP_DURATION: Final = "acp_mcp_call_duration_seconds"

# Deliberate knowledge changes, which are rare enough to count individually and
# valuable enough to want a history of.
MEMORY_OPERATIONS: Final = "acp_memory_operations_total"
SKILL_IMPORTS: Final = "acp_skill_imports_total"

# Readiness as a number, so an alert can fire on a failing dependency without
# anything having to parse the readiness body.
DEPENDENCY_READY: Final = "acp_dependency_ready"
READINESS_DURATION: Final = "acp_readiness_check_duration_seconds"

UPTIME: Final = "acp_uptime_seconds"
BUILD_INFO: Final = "acp_build_info"

_OK: Final = "ok"
_FAILED: Final = "failed"
# Anything outside this is recorded as `other`. A method comes off the request
# line, so it is the one HTTP label a caller has any influence over.
_METHODS: Final = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}
)
# What a request that matched no route is labelled, so an unrouted path can
# never become a series of its own.
UNMATCHED_ROUTE: Final = "unmatched"

SPECS: Final[tuple[MetricSpec, ...]] = (
    MetricSpec(
        name=HTTP_REQUESTS,
        kind="counter",
        help="HTTP requests answered, by method, matched route, and status.",
        labels=("method", "route", "status"),
    ),
    MetricSpec(
        name=HTTP_DURATION,
        kind="summary",
        help="Seconds spent answering an HTTP request, by matched route.",
        labels=("route",),
    ),
    MetricSpec(
        name=MONGODB_DURATION,
        kind="summary",
        help="Seconds one MongoDB command took, as the driver measured it.",
        labels=("command",),
    ),
    MetricSpec(
        name=MONGODB_FAILURES,
        kind="counter",
        help="MongoDB commands that failed.",
        labels=("command",),
    ),
    MetricSpec(
        name=EMBEDDING_DURATION,
        kind="summary",
        help="Seconds spent generating embeddings, by operation.",
        labels=("operation",),
    ),
    MetricSpec(
        name=EMBEDDING_FAILURES,
        kind="counter",
        help="Embedding operations that failed.",
        labels=("operation",),
    ),
    MetricSpec(
        name=SEARCH_DURATION,
        kind="summary",
        help="Seconds one index query took, by retrieval method and collection.",
        labels=("method", "collection"),
    ),
    MetricSpec(
        name=SEARCH_FAILURES,
        kind="counter",
        help="Index queries that could not be served, by method and collection.",
        labels=("method", "collection"),
    ),
    MetricSpec(
        name=RETRIEVAL_DURATION,
        kind="summary",
        help="Seconds one whole retrieval took, including ranking and budgeting.",
        labels=("operation",),
    ),
    MetricSpec(
        name=RETRIEVAL_RESULTS,
        kind="summary",
        help="Results one retrieval returned.",
        labels=("operation",),
    ),
    MetricSpec(
        name=MCP_CALLS,
        kind="counter",
        help="MCP tool calls, by tool and outcome.",
        labels=("tool", "outcome"),
    ),
    MetricSpec(
        name=MCP_DURATION,
        kind="summary",
        help="Seconds one MCP tool call took, by tool.",
        labels=("tool",),
    ),
    MetricSpec(
        name=MEMORY_OPERATIONS,
        kind="counter",
        help="Memory writes, by operation and outcome.",
        labels=("operation", "outcome"),
    ),
    MetricSpec(
        name=SKILL_IMPORTS,
        kind="counter",
        help="Skills seen by an import, by what the import did with each.",
        labels=("outcome",),
    ),
    MetricSpec(
        name=DEPENDENCY_READY,
        kind="gauge",
        help="Whether one dependency passed its last readiness check.",
        labels=("dependency",),
    ),
    MetricSpec(
        name=READINESS_DURATION,
        kind="summary",
        help="Seconds one readiness check took to evaluate every dependency.",
    ),
    MetricSpec(
        name=UPTIME,
        kind="gauge",
        help="Seconds since this process started.",
    ),
    MetricSpec(
        name=BUILD_INFO,
        kind="gauge",
        help="Always 1, labelled with the version of the running build.",
        labels=("version",),
    ),
)

REGISTRY: Final = MetricsRegistry(SPECS)

_started = monotonic()


def uptime_seconds() -> float:
    """Return how long this process has been running."""
    return monotonic() - _started


def record_http_request(*, method: str, route: str, status_code: int, seconds: float) -> None:
    """Count one answered HTTP request and record how long it took."""
    REGISTRY.increment(
        HTTP_REQUESTS,
        method=_method(method),
        route=route,
        status=str(status_code),
    )
    REGISTRY.observe(HTTP_DURATION, seconds, route=route)


def record_mongodb_command(*, command: str, seconds: float, failed: bool = False) -> None:
    """Record one MongoDB command's duration, and whether it failed.

    Only the command's name is taken. The command document itself carries the
    filter, the pipeline, and the documents being written, so it is never read
    here and never reaches a metric or a log.
    """
    REGISTRY.observe(MONGODB_DURATION, seconds, command=command)
    if failed:
        REGISTRY.increment(MONGODB_FAILURES, command=command)


def record_embedding(*, operation: str, seconds: float, failed: bool = False) -> None:
    """Record one embedding operation's duration, and whether it failed."""
    REGISTRY.observe(EMBEDDING_DURATION, seconds, operation=operation)
    if failed:
        REGISTRY.increment(EMBEDDING_FAILURES, operation=operation)


def record_search(*, method: str, collection: str, seconds: float, failed: bool = False) -> None:
    """Record one index query's duration, and whether it could be served.

    Vector and lexical failures are counted apart because they fail apart: a
    rebuilt vector index takes semantic search down while lexical search keeps
    answering, and one number covering both would hide exactly that.
    """
    REGISTRY.observe(SEARCH_DURATION, seconds, method=method, collection=collection)
    if failed:
        REGISTRY.increment(SEARCH_FAILURES, method=method, collection=collection)


def record_retrieval(*, operation: str, seconds: float, result_count: int) -> None:
    """Record one whole retrieval: how long it took and how much it found."""
    REGISTRY.observe(RETRIEVAL_DURATION, seconds, operation=operation)
    REGISTRY.observe(RETRIEVAL_RESULTS, float(result_count), operation=operation)


def record_mcp_call(*, tool: str, outcome: str, seconds: float) -> None:
    """Count one MCP tool call and record how long the server took over it."""
    REGISTRY.increment(MCP_CALLS, tool=tool, outcome=outcome)
    REGISTRY.observe(MCP_DURATION, seconds, tool=tool)


def record_memory_operation(*, operation: str, outcome: str = _OK) -> None:
    """Count one attempt to write or correct a memory."""
    REGISTRY.increment(MEMORY_OPERATIONS, operation=operation, outcome=outcome)


def record_skill_import(*, outcome: str, count: int = 1) -> None:
    """Count what an import did with one skill, or with several at once."""
    if count:
        REGISTRY.increment(SKILL_IMPORTS, float(count), outcome=outcome)


def record_readiness(*, dependency: str, ready: bool) -> None:
    """Record whether one dependency passed the readiness check that just ran."""
    REGISTRY.set(DEPENDENCY_READY, 1.0 if ready else 0.0, dependency=dependency)


def record_readiness_duration(seconds: float) -> None:
    """Record how long it took to evaluate every dependency."""
    REGISTRY.observe(READINESS_DURATION, seconds)


def record_build_info(version: str = __version__) -> None:
    """Publish the running version as a label, the way a build is identified."""
    REGISTRY.set(BUILD_INFO, 1.0, version=version)


def snapshot() -> str:
    """Render every metric, refreshing the ones that describe this moment."""
    REGISTRY.set(UPTIME, uptime_seconds())
    return REGISTRY.render()


def _method(method: str) -> str:
    """Reduce a request method to one of the known ones.

    A method arrives on the request line, so it is the one part of an HTTP
    label a caller chooses. Anything unrecognized becomes `other` rather than a
    series named after whatever was sent.
    """
    normalized = method.upper()
    return normalized if normalized in _METHODS else "other"


def failure(failed: bool) -> str:
    """Name an outcome the way every counter here spells one."""
    return _FAILED if failed else _OK
