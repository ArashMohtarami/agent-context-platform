"""V4 Phase 10 limits, deadlines, and replay-ledger unit contracts."""

import asyncio
from datetime import datetime

import pytest

from app.mcp import server
from app.mcp.auth import build_authorization
from app.mcp.gateway_controls import (
    ClientLimitPolicy,
    GatewayRateLimiter,
    McpGatewayControls,
    RateLimitExceededError,
)
from app.mcp.middleware.client_context import ClientRequestContext, bind_client_context
from app.mcp.schemas import ToolArguments
from app.mcp.server import dispatch
from app.mcp.tools import TOOLS_BY_NAME
from app.mcp.tools.base import ToolDefinition, ToolResult
from app.models.agent import AgentClientType
from app.models.idempotency import IdempotencyRecordDocument, digest_payload
from app.repositories.errors import DuplicateDocumentError
from app.services.gateway_idempotency_service import (
    GatewayIdempotencyService,
    IdempotencyConflictError,
    IdempotencyInProgressError,
)
from tests.mcp_doubles import RecordedApi, api_client


def policy(**changes: int) -> ClientLimitPolicy:
    """Use deliberately tiny independent budgets for control tests."""
    values = {
        "requests_per_minute": 2,
        "concurrent_mcp_calls": 1,
        "writes_per_minute": 1,
        "research_ingestions_per_hour": 1,
        "long_running_jobs_per_client": 1,
    }
    values.update(changes)
    return ClientLimitPolicy(**values)


@pytest.mark.asyncio
async def test_rate_limits_are_per_client_not_a_shared_codex_claude_budget() -> None:
    limiter = GatewayRateLimiter(policy(requests_per_minute=1))

    async with limiter.reserve(
        "claude-main", writes=False, execution_class="fast", research_ingestion=False
    ):
        pass
    with pytest.raises(RateLimitExceededError):
        async with limiter.reserve(
            "claude-main", writes=False, execution_class="fast", research_ingestion=False
        ):
            pass
    async with limiter.reserve(
        "codex-main", writes=False, execution_class="fast", research_ingestion=False
    ):
        pass


@pytest.mark.asyncio
async def test_concurrent_and_long_running_budgets_refuse_without_a_hidden_queue() -> None:
    limiter = GatewayRateLimiter(policy())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_slot() -> None:
        async with limiter.reserve(
            "claude-main", writes=True, execution_class="long_running", research_ingestion=True
        ):
            entered.set()
            await release.wait()

    task = asyncio.create_task(hold_slot())
    await entered.wait()
    with pytest.raises(RateLimitExceededError):
        async with limiter.reserve(
            "claude-main", writes=True, execution_class="long_running", research_ingestion=True
        ):
            pass
    # The other client receives an entirely independent long-running slot.
    async with limiter.reserve(
        "codex-main", writes=True, execution_class="long_running", research_ingestion=True
    ):
        pass
    release.set()
    await task


class Ledger:
    """Small in-memory repository double preserving natural-key races."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], IdempotencyRecordDocument] = {}

    async def create(self, document: IdempotencyRecordDocument) -> IdempotencyRecordDocument:
        key = (document.client_id, document.operation, document.key_hash)
        if key in self.records:
            raise DuplicateDocumentError("exists")
        self.records[key] = document
        return document

    async def get(
        self, *, client_id: str, operation: str, key_hash: str
    ) -> IdempotencyRecordDocument | None:
        return self.records.get((client_id, operation, key_hash))

    async def complete(
        self,
        document: IdempotencyRecordDocument,
        *,
        result_text: str,
        result_data: dict[str, object],
    ) -> IdempotencyRecordDocument | None:
        current = self.records.get((document.client_id, document.operation, document.key_hash))
        if current is None or current.status != "in_progress":
            return None
        completed = IdempotencyRecordDocument.model_validate(
            {
                **current.model_dump(by_alias=True, mode="python"),
                "status": "completed",
                "result_text": result_text,
                "result_data": result_data,
            }
        )
        self.records[(document.client_id, document.operation, document.key_hash)] = completed
        return completed

    async def abandon(self, document: IdempotencyRecordDocument) -> None:
        self.records.pop((document.client_id, document.operation, document.key_hash), None)

    async def prune_expired(self, now: datetime) -> None:
        self.records = {
            key: record for key, record in self.records.items() if record.expires_at > now
        }


@pytest.mark.asyncio
async def test_idempotency_replays_only_the_same_client_operation_and_payload() -> None:
    ledger = Ledger()
    service = GatewayIdempotencyService(ledger, retention_seconds=60)
    payload = digest_payload({"project_id": "payment-service", "title": "Retries"})

    created = await service.acquire(
        client_id="claude-main",
        operation="save_context",
        idempotency_key="retry-1",
        payload_hash=payload,
    )
    assert created.replay is False
    assert "retry-1" not in created.document.model_dump_json()
    await service.complete(
        created.document,
        result_text="Stored project context",
        result_data={"context": {"context_id": "context-1"}},
    )

    replay = await service.acquire(
        client_id="claude-main",
        operation="save_context",
        idempotency_key="retry-1",
        payload_hash=payload,
    )
    assert replay.replay is True
    assert replay.document.result_data == {"context": {"context_id": "context-1"}}

    with pytest.raises(IdempotencyConflictError):
        await service.acquire(
            client_id="claude-main",
            operation="save_context",
            idempotency_key="retry-1",
            payload_hash=digest_payload({"project_id": "payment-service", "title": "Changed"}),
        )
    # The same opaque key is independent for another credential-derived client.
    codex = await service.acquire(
        client_id="codex-main",
        operation="save_context",
        idempotency_key="retry-1",
        payload_hash=payload,
    )
    assert codex.replay is False


@pytest.mark.asyncio
async def test_pending_retry_is_not_blindly_reexecuted() -> None:
    service = GatewayIdempotencyService(Ledger(), retention_seconds=60)
    reservation = await service.acquire(
        client_id="claude-main",
        operation="save_memory",
        idempotency_key="retry-pending",
        payload_hash="a" * 64,
    )
    assert reservation.replay is False
    with pytest.raises(IdempotencyInProgressError):
        await service.acquire(
            client_id="claude-main",
            operation="save_memory",
            idempotency_key="retry-pending",
            payload_hash="a" * 64,
        )


class _EmptyArguments(ToolArguments):
    """Minimal test-only input for a deliberately slow MCP handler."""


@pytest.mark.asyncio
async def test_execution_deadline_translates_to_the_stable_mcp_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_handler(*_: object) -> ToolResult:
        await asyncio.sleep(0.05)
        return ToolResult(text="late", data={})

    slow = ToolDefinition(
        name="slow_test",
        title="Slow test",
        description="A test-only bounded operation.",
        scope="search:read",
        arguments=_EmptyArguments,
        handler=slow_handler,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(server, "TOOLS_BY_NAME", {"slow_test": slow})
    controls = McpGatewayControls(
        limiter=GatewayRateLimiter(policy()),
        fast_timeout_seconds=0.001,
        standard_timeout_seconds=0.001,
        long_running_timeout_seconds=0.001,
    )
    api = RecordedApi()
    client = api_client(api)
    try:
        result = await dispatch(
            "slow_test",
            {},
            client=client,
            authorization=build_authorization(["search:read"]),
            controls=controls,
        )
    finally:
        await client.close()

    assert result.isError is True
    assert result.structuredContent == {
        "error": {
            "code": "OPERATION_TIMEOUT",
            "message": "the operation exceeded its execution deadline",
        }
    }


@pytest.mark.asyncio
async def test_mcp_rate_refusal_uses_the_stable_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    async def immediate_handler(*_: object) -> ToolResult:
        return ToolResult(text="done", data={})

    definition = ToolDefinition(
        name="limited_test",
        title="Limited test",
        description="A test-only rate-limited operation.",
        scope="search:read",
        arguments=_EmptyArguments,
        handler=immediate_handler,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(server, "TOOLS_BY_NAME", {"limited_test": definition})
    controls = McpGatewayControls(
        limiter=GatewayRateLimiter(policy(requests_per_minute=1)),
        fast_timeout_seconds=1.0,
        standard_timeout_seconds=1.0,
        long_running_timeout_seconds=1.0,
    )
    context = ClientRequestContext(
        org_id="org-test",
        key_id="a" * 16,
        allowed_profile_ids=None,
        request_id="request-1",
        client_id="claude-main",
        client_type=AgentClientType.CLAUDE_CODE,
        scopes=("search:read",),
        allowed_project_ids=None,
    )
    api = RecordedApi()
    client = api_client(api)
    try:
        with bind_client_context(context):
            first = await dispatch(
                "limited_test",
                {},
                client=client,
                authorization=build_authorization(["search:read"]),
                controls=controls,
            )
            limited = await dispatch(
                "limited_test",
                {},
                client=client,
                authorization=build_authorization(["search:read"]),
                controls=controls,
            )
    finally:
        await client.close()

    assert first.isError is False
    assert limited.structuredContent == {
        "error": {
            "code": "RATE_LIMITED",
            "message": "request rate limit reached; try again shortly",
        }
    }


def test_existing_v3_ingestion_remains_the_declared_long_running_job_path() -> None:
    """Phase 10 routes the existing V3 job-returning ingestion tool, not a new backend."""
    definition = TOOLS_BY_NAME["ingest_research_source"]

    assert definition.execution_class == "long_running"
    assert definition.writes is True


@pytest.fixture(autouse=True)
def admitted_tenant_for_domain_regression(tenant_runtime):
    """Existing behavior runs inside one explicitly admitted organization."""
    yield
