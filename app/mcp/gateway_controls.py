"""Replaceable, single-instance MCP rate limits and execution deadlines."""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal, Protocol

from app.core.config import McpSettings

ExecutionClass = Literal["fast", "standard", "long_running"]


class RateLimitExceededError(Exception):
    """A client exceeded a bounded gateway budget without exposing its quota details."""


@dataclass(frozen=True, slots=True)
class ClientLimitPolicy:
    """Configured limits that apply independently to every registered client."""

    requests_per_minute: int
    concurrent_mcp_calls: int
    writes_per_minute: int
    research_ingestions_per_hour: int
    long_running_jobs_per_client: int


@dataclass(frozen=True, slots=True)
class LimitRequest:
    """The bounded characteristics of one already-authorized MCP operation."""

    client_id: str
    writes: bool
    execution_class: ExecutionClass
    research_ingestion: bool


@dataclass(frozen=True, slots=True)
class LimitLease:
    """An opaque reservation which must be released once the operation finishes."""

    client_id: str
    long_running: bool


class ClientLimitBackend(Protocol):
    """Backend seam for a future shared/distributed limiter implementation."""

    async def acquire(self, request: LimitRequest, policy: ClientLimitPolicy) -> LimitLease: ...

    async def release(self, lease: LimitLease) -> None: ...


@dataclass(slots=True)
class _ClientWindow:
    requests: deque[float] = field(default_factory=deque)
    writes: deque[float] = field(default_factory=deque)
    ingestions: deque[float] = field(default_factory=deque)
    active_calls: int = 0
    active_long_running: int = 0


class InMemoryClientLimitBackend:
    """One process-local limiter for the documented single-instance deployment.

    It intentionally has no queue: when a client is already at its active-call
    budget, callers receive a stable refusal instead of accumulating a hidden
    backlog.  A multi-instance deployment can replace this class through the
    ``ClientLimitBackend`` protocol without changing MCP tools or services.
    """

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._windows: dict[str, _ClientWindow] = {}

    async def acquire(self, request: LimitRequest, policy: ClientLimitPolicy) -> LimitLease:
        """Atomically check and consume one client's independent budgets."""
        now = self._clock()
        async with self._lock:
            window = self._windows.setdefault(request.client_id, _ClientWindow())
            self._prune(window, now)
            if len(window.requests) >= policy.requests_per_minute:
                raise RateLimitExceededError("request rate limit reached; try again shortly")
            if request.writes and len(window.writes) >= policy.writes_per_minute:
                raise RateLimitExceededError("write rate limit reached; try again shortly")
            if (
                request.research_ingestion
                and len(window.ingestions) >= policy.research_ingestions_per_hour
            ):
                raise RateLimitExceededError("research ingestion limit reached; try again later")
            if window.active_calls >= policy.concurrent_mcp_calls:
                raise RateLimitExceededError("concurrent MCP call limit reached; try again shortly")
            long_running = request.execution_class == "long_running"
            if long_running and window.active_long_running >= policy.long_running_jobs_per_client:
                raise RateLimitExceededError("long-running job limit reached; try again later")
            window.requests.append(now)
            if request.writes:
                window.writes.append(now)
            if request.research_ingestion:
                window.ingestions.append(now)
            window.active_calls += 1
            if long_running:
                window.active_long_running += 1
            return LimitLease(client_id=request.client_id, long_running=long_running)

    async def release(self, lease: LimitLease) -> None:
        """Release only the active-count portion of a completed reservation."""
        async with self._lock:
            window = self._windows.get(lease.client_id)
            if window is None:  # pragma: no cover - defensive against a custom reset
                return
            window.active_calls = max(0, window.active_calls - 1)
            if lease.long_running:
                window.active_long_running = max(0, window.active_long_running - 1)
            self._prune(window, self._clock())
            if not (
                window.requests
                or window.writes
                or window.ingestions
                or window.active_calls
                or window.active_long_running
            ):
                self._windows.pop(lease.client_id, None)

    @staticmethod
    def _prune(window: _ClientWindow, now: float) -> None:
        while window.requests and now - window.requests[0] >= 60.0:
            window.requests.popleft()
        while window.writes and now - window.writes[0] >= 60.0:
            window.writes.popleft()
        while window.ingestions and now - window.ingestions[0] >= 3_600.0:
            window.ingestions.popleft()


class GatewayRateLimiter:
    """Apply configured per-client limits through a replaceable backend."""

    def __init__(
        self,
        policy: ClientLimitPolicy,
        *,
        backend: ClientLimitBackend | None = None,
    ) -> None:
        self._policy = policy
        self._backend = backend or InMemoryClientLimitBackend()

    @asynccontextmanager
    async def reserve(
        self,
        client_id: str | None,
        *,
        writes: bool,
        execution_class: ExecutionClass,
        research_ingestion: bool,
    ) -> AsyncIterator[None]:
        """Reserve a client budget; legacy unlinked keys retain existing behavior."""
        if client_id is None:
            yield
            return
        lease = await self._backend.acquire(
            LimitRequest(
                client_id=client_id,
                writes=writes,
                execution_class=execution_class,
                research_ingestion=research_ingestion,
            ),
            self._policy,
        )
        try:
            yield
        finally:
            await self._backend.release(lease)


@dataclass(frozen=True, slots=True)
class McpGatewayControls:
    """Gateway-wide limiter and explicit execution-class timeout policy."""

    limiter: GatewayRateLimiter
    fast_timeout_seconds: float
    standard_timeout_seconds: float
    long_running_timeout_seconds: float

    @classmethod
    def from_settings(cls, settings: McpSettings) -> "McpGatewayControls":
        """Build the documented self-hosted policy from environment settings."""
        return cls(
            limiter=GatewayRateLimiter(
                ClientLimitPolicy(
                    requests_per_minute=settings.mcp_requests_per_minute,
                    concurrent_mcp_calls=settings.mcp_concurrent_calls,
                    writes_per_minute=settings.mcp_writes_per_minute,
                    research_ingestions_per_hour=settings.mcp_research_ingestions_per_hour,
                    long_running_jobs_per_client=settings.mcp_long_running_jobs_per_client,
                )
            ),
            fast_timeout_seconds=settings.mcp_fast_timeout_seconds,
            standard_timeout_seconds=settings.mcp_standard_timeout_seconds,
            long_running_timeout_seconds=settings.mcp_long_running_timeout_seconds,
        )

    @classmethod
    def defaults(cls) -> "McpGatewayControls":
        """Use default settings for direct programmatic MCP server construction."""
        return cls.from_settings(McpSettings())

    def timeout_for(self, execution_class: ExecutionClass) -> float:
        """Return one bounded deadline without leaking deployment configuration."""
        if execution_class == "fast":
            return self.fast_timeout_seconds
        if execution_class == "standard":
            return self.standard_timeout_seconds
        return self.long_running_timeout_seconds
