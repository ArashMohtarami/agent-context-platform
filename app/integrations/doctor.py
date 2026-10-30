"""Bounded, redacted diagnostics over the existing REST and MCP contracts.

Only fixed codes/counts leave this module. Remote text, headers, URLs, credential
contents, local paths and retrieved Markdown are never diagnostic output.
"""

import asyncio
import json
import os
import re
import secrets
import ssl
import stat
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from app.integrations import DEFAULT_INTEGRATION_REGISTRY
from app.integrations.connection import STAGES, ProbeSelection, read_only_scopes
from app.mcp.schemas import (
    ResolveProjectArguments,
    SaveContextArguments,
    SearchKnowledgeArguments,
)
from app.mcp.tools import TOOLS_BY_NAME
from app.schemas.gateway import GatewayIdentityResponse
from app.security.keys import parse

MAX_BYTES = 512 * 1024
MAX_CA_BYTES = 1024 * 1024
NEXT = {
    "configuration": "Check client, origin, project, private token file and reviewed CA file.",
    "transport": "Check the origin, listener and reviewed CA; retain certificate validation.",
    "authentication": "Check credential expiry, revocation and recipient delivery.",
    "capabilities": "Check the shared MCP endpoint and agent:self capability grant.",
    "project": "Select an exact existing project and request its projects:read grant.",
    "retrieval": "Request coupled search/context read for this project; check search readiness.",
    "policy": "Request an approved read-only credential; preview changes no policy.",
    "write": "Check context:write on the disposable project. No automatic retry or cleanup.",
}


class DiagnosisError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def credential_read_only(identity: GatewayIdentityResponse) -> bool:
    """Check both scope projection and coupled tuples; unknown actions fail closed."""
    return read_only_scopes(identity.scopes) and (
        identity.action_resources is None
        or read_only_scopes(
            tuple(g.action for g in identity.action_resources if g.selector != "none")
        )
    )


def tls_context(ca_file: Path | None) -> ssl.SSLContext:
    """Bound explicit CA input before parsing; never accept a device or FIFO."""
    if ca_file is None:
        return ssl.create_default_context()
    try:
        absolute = ca_file.absolute()
        if absolute.resolve() != absolute:
            raise ValueError
        fd = os.open(absolute, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="ascii") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CA_BYTES:
                raise ValueError
            pem = source.read(MAX_CA_BYTES + 1)
        if len(pem) > MAX_CA_BYTES:
            raise ValueError
        return ssl.create_default_context(cadata=pem)
    except (OSError, ValueError, UnicodeError):
        raise DiagnosisError("CA_TRUST_INVALID") from None


def failure_code(error: BaseException) -> str:
    """SDK task groups can wrap a safe refusal; never stringify remote exceptions."""
    if isinstance(error, DiagnosisError):
        return error.code
    if isinstance(error, BaseExceptionGroup):
        codes = [failure_code(child) for child in error.exceptions]
        return next((code for code in codes if code != "CHECK_FAILED"), "CHECK_FAILED")
    if isinstance(error, TimeoutError | httpx.TimeoutException):
        return "DEADLINE_EXCEEDED"
    if isinstance(error, ssl.SSLError | httpx.ConnectError):
        return "TLS_OR_CONNECTION_FAILED"
    return "CHECK_FAILED"


def token_value(path: Path) -> str:
    """Read only a deliberately selected regular, owner-only file, bounded and no symlinks."""
    try:
        absolute = path.absolute()
        if absolute.resolve() != absolute:
            raise ValueError
        fd = os.open(absolute, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            meta = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(meta.st_mode)
                or meta.st_uid != os.getuid()
                or meta.st_mode & 0o077
                or meta.st_size > 512
            ):
                raise ValueError
            token = handle.read(513).strip()
        parse(token)
        return token
    except (OSError, ValueError, UnicodeError):
        raise DiagnosisError("CREDENTIAL_SOURCE_INVALID") from None


async def response(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> tuple[dict[str, Any], httpx.Headers]:
    """Read a bounded response, without following redirects or reflecting its body."""
    async with client.stream(method, path, **kwargs) as result:
        if result.status_code in {401, 403}:
            raise DiagnosisError("UNAUTHENTICATED" if result.status_code == 401 else "FORBIDDEN")
        if not result.is_success:
            raise DiagnosisError("ENDPOINT_REFUSED")
        data = bytearray()
        async for chunk in result.aiter_bytes(chunk_size=8192):
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise DiagnosisError("RESPONSE_TOO_LARGE")
        try:
            body = json.loads(data) if data else {}
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            raise DiagnosisError("INVALID_RESPONSE") from None
        return body, result.headers


class HttpMcp:
    """The shipped gateway uses bounded JSON Streamable HTTP responses."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.headers = {"Accept": "application/json, text/event-stream"}
        self.number = 0

    async def rpc(
        self, method: str, params: dict[str, Any], *, notification: bool = False
    ) -> dict[str, Any]:
        self.number += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = self.number
        body, headers = await response(
            self.client, "POST", "/mcp", headers=self.headers, json=payload
        )
        if method == "initialize":
            session = headers.get("mcp-session-id")
            if not session or not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", session):
                raise DiagnosisError("MCP_SESSION_INVALID")
            self.headers["mcp-session-id"] = session
        if notification:
            return {}
        if body.get("id") != self.number or "result" not in body:
            raise DiagnosisError("MCP_PROTOCOL_ERROR")
        result = body["result"]
        if not isinstance(result, dict):
            raise DiagnosisError("MCP_PROTOCOL_ERROR")
        return result

    async def initialize(self) -> None:
        result = types.InitializeResult.model_validate(
            await self.rpc(
                "initialize",
                {
                    "protocolVersion": types.LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "agent-context-doctor", "version": "1"},
                },
            )
        )
        self.headers["MCP-Protocol-Version"] = str(result.protocolVersion)
        await self.rpc("notifications/initialized", {}, notification=True)

    async def list_tools(self) -> types.ListToolsResult:
        return types.ListToolsResult.model_validate(await self.rpc("tools/list", {}))

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        return types.CallToolResult.model_validate(
            await self.rpc("tools/call", {"name": name, "arguments": arguments})
        )

    async def close(self) -> None:
        if "mcp-session-id" in self.headers:
            await response(self.client, "DELETE", "/mcp", headers=self.headers)


@asynccontextmanager
async def mcp_session(
    selection: ProbeSelection, client: httpx.AsyncClient, token: str, ca_file: Path | None
) -> AsyncIterator[ClientSession | HttpMcp]:
    integration = DEFAULT_INTEGRATION_REGISTRY.get(selection.integration_id)
    if integration.metadata.transport != "stdio":
        session = HttpMcp(client)
        try:
            yield session
        finally:
            await session.close()
        return
    root = Path(__file__).resolve().parents[2]
    # Fixed reviewed entry point, never execute configuration supplied by a client.
    # -I + _env_file=None prevents inherited Python flags and deployment .env.
    script = (
        "import sys,asyncio; sys.path.insert(0,sys.argv[1]); "
        "from app.core.config import McpSettings; from app.mcp.server import run_stdio; "
        "asyncio.run(run_stdio(McpSettings(_env_file=None)))"
    )
    env = {
        "MCP_API_BASE_URL": selection.origin,
        "MCP_API_TOKEN": token,  # same checked credential; never re-read a racing file
        "MCP_REQUEST_TIMEOUT_SECONDS": "8",
        "LOG_LEVEL": "CRITICAL",
    }
    if ca_file is not None:
        env["MCP_API_CA_FILE"] = str(ca_file.absolute())
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-I", "-c", script, str(root)],
        cwd=str(root),
        env=env,
    )
    with open(os.devnull, "w") as errlog:  # noqa: ASYNC230 -- constant device, never disk/user data
        async with stdio_client(parameters, errlog=errlog) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=10)
            ) as stdio_session:
                yield stdio_session


async def call(
    session: ClientSession | HttpMcp, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    validated = TOOLS_BY_NAME[name].parse(arguments).model_dump(exclude_none=True)
    result = await session.call_tool(name, validated)
    data = result.structuredContent
    if result.isError:
        # Allowlist codes; never return server-provided message/text.
        error = data.get("error", {}) if isinstance(data, dict) else {}
        code = error.get("code")
        raise DiagnosisError(
            code
            if code
            in {
                "FORBIDDEN",
                "PROJECT_FORBIDDEN",
                "UNAUTHENTICATED",
                "NOT_FOUND",
                "RATE_LIMITED",
                "OPERATION_TIMEOUT",
                "SERVICE_UNAVAILABLE",
            }
            else "MCP_TOOL_FAILED"
        )
    if not isinstance(data, dict):
        raise DiagnosisError("INVALID_RESPONSE")
    return data


async def diagnose(
    selection: ProbeSelection, *, token_file: Path | None, ca_file: Path | None = None
) -> dict[str, Any]:
    """Read-only by default. Reports contain fixed messages and counts, never knowledge."""
    stages = [*STAGES, "policy", "write"]
    checks = {
        stage: {
            "stage": stage,
            "status": "not_run",
            "code": "DEPENDENCY_FAILED",
            "next_step": NEXT[stage],
        }
        for stage in stages
    }
    if not selection.write_probe:
        checks["write"].update(status="not_requested", code="OPT_IN_REQUIRED")
    if not selection.require_read_only:
        checks["policy"].update(status="not_requested", code="NOT_REQUESTED")
    report: dict[str, Any] = {
        "ready": False,
        "network_checked": False,
        "server_read_only": None,
        "retrieved_count": None,
        "checks": list(checks.values()),
    }
    stage = "configuration"

    def passed(name: str, code: str = "OK") -> None:
        checks[name].update(status="passed", code=code, next_step="No action required.")

    try:
        if token_file is None:
            raise DiagnosisError("CREDENTIAL_SOURCE_REQUIRED")
        token = token_value(token_file)
        tls = tls_context(ca_file)
        passed(stage)
        async with httpx.AsyncClient(
            base_url=selection.origin,
            verify=tls,
            trust_env=False,
            follow_redirects=False,
            timeout=8,
        ) as client:
            # One overall deadline bounds all stages and stdio child lifetime.
            async with asyncio.timeout(60):
                stage = "transport"
                report["network_checked"] = True
                await response(client, "GET", "/health/live")
                passed(
                    stage,
                    "TLS_VERIFIED" if selection.origin.startswith("https:") else "LOOPBACK_HTTP",
                )
                stage = "authentication"
                client.headers["Authorization"] = "Bearer " + token
                body, _ = await response(client, "GET", "/api/v4/gateway/identity")
                identity = GatewayIdentityResponse.model_validate(body)
                report["server_read_only"] = credential_read_only(identity)
                passed(stage)
                stage = "capabilities"
                async with mcp_session(selection, client, token, ca_file) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if not {"get_platform_capabilities", "resolve_project", "search_knowledge"} <= {
                        t.name for t in tools.tools
                    }:
                        raise DiagnosisError("CAPABILITY_MISSING")
                    capabilities = await call(session, "get_platform_capabilities", {})
                    if not isinstance(capabilities.get("data", {}).get("caller"), dict):
                        raise DiagnosisError("CAPABILITY_INVALID")
                    passed(stage)
                    stage = "project"
                    resolved = await call(
                        session,
                        "resolve_project",
                        ResolveProjectArguments(project_id=selection.project_id).model_dump(
                            exclude_none=True
                        ),
                    )
                    if resolved.get("data", resolved).get("project_id") != selection.project_id:
                        raise DiagnosisError("PROJECT_UNRESOLVED")
                    passed(stage)
                    stage = "retrieval"
                    read = await call(
                        session,
                        "search_knowledge",
                        SearchKnowledgeArguments(
                            query="evaluation connection diagnostic",
                            project_id=selection.project_id,
                            sources=["project_context"],
                            limit=1,
                            token_budget=512,
                        ).model_dump(exclude_none=True),
                    )
                    results = read.get("data", read).get("results")
                    if not isinstance(results, list):
                        raise DiagnosisError("RETRIEVAL_INVALID")
                    report["retrieved_count"] = len(results)
                    passed(stage, "OK" if results else "EMPTY")
                    if selection.require_read_only:
                        stage = "policy"
                        if not report["server_read_only"]:
                            raise DiagnosisError("SERVER_POLICY_HAS_WRITES")
                        passed(stage, "SERVER_READ_ONLY")
                    if selection.write_probe:
                        stage = "write"
                        try:
                            await call(
                                session,
                                "save_context",
                                SaveContextArguments(
                                    project_id=selection.project_id,
                                    type="convention",
                                    title="Disposable connection diagnostic",
                                    content="Opt-in disposable connection probe.",
                                    importance=0.1,
                                    external_id="doctor-" + secrets.token_hex(12),
                                ).model_dump(exclude_none=True),
                            )
                        except DiagnosisError as error:
                            if selection.require_read_only and error.code in {
                                "FORBIDDEN",
                                "PROJECT_FORBIDDEN",
                            }:
                                passed(stage, "WRITE_REFUSED")
                            else:
                                raise
                        else:
                            if selection.require_read_only:
                                raise DiagnosisError("UNEXPECTED_WRITE_ALLOWED")
                            passed(stage, "PROBE_CREATED")
        report["ready"] = True
    except Exception as error:
        checks[stage].update(status="failed", code=failure_code(error))
    return report
