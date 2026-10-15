"""Doubles for the MCP tests.

Every tool's real contract is the request it issues, so these record the actual
HTTP calls rather than stubbing the tool functions: a tool that called the
wrong endpoint, dropped a filter, or invented a query parameter fails here.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.mcp.client import ContextApiClient
from app.security.scopes import ALL_SCOPES

Responder = Callable[[httpx.Request], httpx.Response]


@dataclass
class RecordedApi:
    """One fake Context API that remembers every request it was sent."""

    responses: dict[tuple[str, str], Any] = field(default_factory=dict)
    status: dict[tuple[str, str], int] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    error: Exception | None = None

    def respond(self, method: str, path: str, body: Any, *, status_code: int = 200) -> None:
        """Answer one method and path with a fixed body."""
        self.responses[(method, path)] = body
        self.status[(method, path)] = status_code

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        key = (request.method, request.url.path)
        if key not in self.responses:
            return httpx.Response(404, json={"detail": "no such route in this double"})
        return httpx.Response(self.status[key], json=self.responses[key])

    @property
    def last(self) -> httpx.Request:
        """Return the most recent request."""
        return self.requests[-1]

    @property
    def paths(self) -> list[str]:
        """Return every path requested, in order."""
        return [request.url.path for request in self.requests]

    def body(self, index: int = -1) -> dict[str, Any]:
        """Decode one recorded request's JSON body."""
        content = self.requests[index].content
        decoded = json.loads(content) if content else {}
        return decoded if isinstance(decoded, dict) else {}

    def query(self, index: int = -1) -> dict[str, str]:
        """Return one recorded request's query parameters."""
        return dict(self.requests[index].url.params)


def api_client(
    api: RecordedApi | Responder,
    *,
    token: str | None = None,
    base_url: str = "http://context-api.invalid",
) -> ContextApiClient:
    """Build a client bound to a fake API."""

    class TenantApiHarness(ContextApiClient):
        async def gateway_identity(self, *, session_id=None):
            if isinstance(api, RecordedApi) and (
                api.error is not None or ("GET", "/api/v4/gateway/identity") in api.responses
            ):
                return await super().gateway_identity(session_id=session_id)
            # Tool/HTTP mapping tests inject an admitted test identity. Gateway
            # contract tests explicitly arrange the preflight response instead.
            return {
                "org_id": "org-test",
                "key_id": "a" * 16,
                "client_id": None,
                "client_type": None,
                "scopes": list(ALL_SCOPES),
                "allowed_project_ids": None,
                "allowed_profile_ids": None,
            }

    return TenantApiHarness(base_url, token=token, transport=httpx.MockTransport(api))


def search_body(
    results: Sequence[Mapping[str, Any]] = (),
    *,
    query: str = "payment idempotency",
    project_id: str | None = None,
) -> dict[str, Any]:
    """Build one unified-search response body."""
    return {
        "query": query,
        "project_id": project_id,
        "token_count": sum(int(item.get("tokens", 0)) for item in results),
        "token_budget": 4_000,
        "results": list(results),
        "retrieval": {
            "query_id": "0123456789abcdef0123456789abcdef",
            "retrieval_method": "hybrid",
            "requested_sources": ["project_context", "active_skill", "memory"],
            "searched_sources": ["project_context", "active_skill", "memory"],
            "skipped_sources": [],
            "latency_ms": 12.5,
            "diagnostics": {
                "methods": ["vector", "lexical"],
                "weights": {"vector": 0.45},
                "vector_matches": 2,
                "lexical_matches": 1,
                "merged": 2,
                "filtered_out": 0,
                "duplicates_removed": 0,
                "ranked": 2,
                "dropped_for_budget": 0,
            },
        },
    }


def search_result(
    *,
    source: str = "project_context",
    title: str = "Payment idempotency strategy",
    content: str = "Payment requests carry a UUID4 idempotency key.",
    score: float = 0.9312,
    tokens: int = 28,
    project_id: str | None = "sampleco-payment",
) -> dict[str, Any]:
    """Build one unified-search result item."""
    return {
        "key": f"{source}:6720a1b2c3d4e5f60718293a",
        "source": source,
        "title": title,
        "content": content,
        "score": score,
        "tokens": tokens,
        "metadata": {"project_id": project_id, "type": "architecture_decision"},
        "explanation": None,
    }


def context_record(project_id: str = "sampleco-payment", **overrides: Any) -> dict[str, Any]:
    """Build one stored context item as the API returns it."""
    record = {
        "context_id": "6720a1b2c3d4e5f60718293a",
        "project_id": project_id,
        "type": "architecture_decision",
        "title": "Payment idempotency strategy",
        "content": "Payment requests carry a UUID4 idempotency key.",
        "importance": 0.9,
        "tags": ["payments"],
        "source": "mcp",
        "status": "active",
        "external_id": None,
        "revision": 1,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }
    record.update(overrides)
    return record


def memory_record(project_id: str | None = None, **overrides: Any) -> dict[str, Any]:
    """Build one stored memory as the API returns it."""
    record: dict[str, Any] = {
        "memory_id": "6720a1b2c3d4e5f60718293b",
        "scope": "global" if project_id is None else "project",
        "project_id": project_id,
        "type": "preference",
        "content": "Use strict typing in every Python project.",
        "summary": "Strict typing everywhere",
        "importance": 0.8,
        "confidence": 0.95,
        "tags": ["python"],
        "source": "mcp",
        "status": "active",
        "external_id": None,
        "supersedes": None,
        "superseded_by": None,
        "revision": 1,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }
    record.update(overrides)
    return record


def skill_record(**overrides: Any) -> dict[str, Any]:
    """Build one skill summary as the API returns it."""
    record: dict[str, Any] = {
        "skill_id": "fastapi-production",
        "name": "FastAPI Production Engineering",
        "version": "1.0.0",
        "description": "Production practices for FastAPI services.",
        "category": "backend",
        "tags": ["python", "fastapi"],
        "trust_level": "trusted",
        "status": "active",
        "is_active": True,
        "source_path": "skills/fastapi-production.md",
        "content_hash": "a" * 64,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }
    record.update(overrides)
    return record


def project_record(project_id: str = "sampleco-payment", **overrides: Any) -> dict[str, Any]:
    """Build one project as the API returns it."""
    record: dict[str, Any] = {
        "project_id": project_id,
        "name": "Sample Company Payment Service",
        "description": "Payment orchestration",
        "repository": "git@example.invalid:sampleco/payment.git",
        "environment": "production",
        "tags": ["payments"],
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }
    record.update(overrides)
    return record


def readiness_body(*, ready: bool = True) -> dict[str, Any]:
    """Build one readiness response body."""
    return {
        "status": "ready" if ready else "not_ready",
        "mongodb": True,
        "database": True,
        "replica_set": True,
        "mongot": ready,
        "embedding": {
            "enabled": True,
            "available": True,
            "ready": True,
            "model": "sentence-transformers/all-MiniLM-L6-v2",
            "dimensions": 384,
            "device": "cpu",
        },
        "vector_search": {
            "enabled": True,
            "available": True,
            "queryable": ready,
            "index": "skill_chunk_vector_index",
            "ready": ready,
        },
        "lexical_search": {
            "available": True,
            "queryable": True,
            "index": "skill_chunk_text_index",
            "ready": True,
        },
        "context_search": {"enabled": True, "ready": True},
        "memory_search": {"enabled": True, "ready": True},
    }
