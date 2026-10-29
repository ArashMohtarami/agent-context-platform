"""Fictional two-client MCP persistence handoff. No provider, installer or config edit."""

import argparse
import asyncio
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from bson import ObjectId  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field, model_validator  # noqa: E402

from app.integrations.connection import ConnectionSelection, ProbeSelection  # noqa: E402
from app.integrations.doctor import (  # noqa: E402
    DiagnosisError,
    failure_code,
    mcp_session,
    response,
    tls_context,
    token_value,
)
from app.mcp.contract import McpErrorCode  # noqa: E402
from app.mcp.schemas import SaveContextArguments  # noqa: E402
from app.mcp.tools import TOOLS_BY_NAME  # noqa: E402
from app.mcp.tools.memory import MEMORY_SOURCE  # noqa: E402
from app.models.common import Identifier  # noqa: E402
from app.projects.types import context_key  # noqa: E402
from app.schemas.gateway import GatewayIdentityResponse  # noqa: E402

QUERY = "Notes API retry behavior Idempotency-Key"
PRIVATE_QUERY = "Fictional private handoff sentinel"


class HandoffError(Exception):
    """Fixed codes only: remote text, tokens and local paths are never errors."""


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    origin: str
    project_id: Identifier
    negative_project_id: Identifier
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")

    @model_validator(mode="after")
    def explicit_boundaries(self) -> "Selection":
        selected = ConnectionSelection(
            integration_id="codex", origin=self.origin, project_id=self.project_id
        )
        if not selected.origin.startswith("https:"):
            raise ValueError("handoff requires verified HTTPS")
        if self.project_id == self.negative_project_id:
            raise ValueError("select a distinct permitted negative project")
        return self

    def connection(self, transport: str) -> ProbeSelection:
        return ProbeSelection(
            integration_id="codex" if transport == "stdio" else "custom",
            origin=self.origin,
            project_id=self.project_id,
        )


def fact(selection: Selection) -> dict[str, Any]:
    template = json.loads(Path(__file__).with_name("fact.json").read_text())
    template.update(
        project_id=selection.project_id,
        external_id="handoff-" + selection.run_id + "-fact-v1",
        tags=["notes", "idempotency", "handoff-" + selection.run_id],
    )
    return SaveContextArguments.model_validate(template).model_dump(exclude_none=True)


def provenance() -> list[dict[str, str]]:
    return [
        {"path": name, "sha256": hashlib.sha256((ROOT / name).read_bytes()).hexdigest()}
        for name in (
            "examples/starter-notes-api/notes_app.py",
            "examples/starter-notes-api/test_app.py",
            "examples/agent-handoff/fact.json",
        )
    ]


def data(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("data", result)
    if not isinstance(value, dict):
        raise HandoffError("INVALID_CHECKPOINT")
    return value


def protocol_error(body: Any) -> str:
    """Export only the maintained error enum; never a server message or input."""
    try:
        return McpErrorCode(body["error"]["code"]).value
    except (KeyError, TypeError, ValueError):
        return "MCP_TOOL_FAILED"


async def call(session: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    validated = TOOLS_BY_NAME[name].parse(arguments).model_dump(exclude_none=True)
    result = await session.call_tool(name, validated)
    body = result.structuredContent
    if result.isError:
        code = protocol_error(body)
        emit("tool_refused", tool=name, code=code)
        raise DiagnosisError(code)
    if not isinstance(body, dict):
        raise HandoffError("INVALID_CHECKPOINT")
    return body


def same_record(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    fields = ("context_id", "revision", "project_id", "content", "title", "source", "external_id")
    if any(field not in expected or actual.get(field) != expected[field] for field in fields):
        raise HandoffError("RECORD_MISMATCH")


def validate_saved(saved: dict[str, Any], requested: dict[str, Any]) -> None:
    if (
        not isinstance(saved.get("context_id"), str)
        or not isinstance(saved.get("revision"), int)
        or saved["revision"] < 1
        or any(saved.get(k) != requested[k] for k in ("project_id", "content", "source", "title"))
    ):
        raise HandoffError("EXTERNAL_ID_CONTENT_MISMATCH")


def gate_path(path: Path, *, fresh: bool) -> Path:
    absolute = path.absolute()
    parent = absolute.parent
    if (
        absolute.resolve() != absolute
        or not parent.is_dir()
        or parent.stat().st_uid != os.getuid()
        or parent.stat().st_mode & 0o077
        or (fresh and os.path.lexists(absolute))
    ):
        raise HandoffError("REVOCATION_SIGNAL_INVALID")
    return absolute


async def wait_for_recipient(path: Path, deadline_seconds: float = 120) -> None:
    """A signal requests a check; only the subsequent server denial proves revoke."""
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        gate_path(path, fresh=False)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            await asyncio.sleep(0.2)
            continue
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > 32
                or handle.read(33) != b"revoked\n"
            ):
                raise HandoffError("REVOCATION_SIGNAL_INVALID")
        return
    raise HandoffError("REVOCATION_NOT_CONFIRMED")


async def resolved(session: Any, project: str) -> None:
    result = data(await call(session, "resolve_project", {"project_id": project}))
    if result.get("project_id") != project:
        raise HandoffError("PROJECT_UNRESOLVED")


async def indexed(session: Any, name: str, arguments: dict[str, Any], key: str) -> dict[str, Any]:
    # Index lag is visible and bounded. A direct read cannot substitute for search.
    deadline = time.monotonic() + 45
    while True:
        result = data(await call(session, name, arguments))
        for row in result.get("results", []):
            if row.get("key") == key:
                return result
        if time.monotonic() >= deadline:
            raise HandoffError("INDEXED_RECORD_MISSING")
        await asyncio.sleep(0.5)


async def refused(session: Any, name: str, arguments: dict[str, Any], codes: set[str]) -> str:
    try:
        await call(session, name, arguments)
    except DiagnosisError as error:
        if error.code in codes:
            return error.code
        raise
    raise HandoffError("UNEXPECTED_ACCESS_ALLOWED")


def emit(checkpoint: str, **values: Any) -> None:
    print(json.dumps({"checkpoint": checkpoint, **values}), flush=True)


async def run(
    selection: Selection, a_file: Path, b_file: Path, ca_file: Path | None, gate: Path
) -> dict[str, Any]:
    gate = gate_path(gate, fresh=True)
    a_token, b_token = token_value(a_file), token_value(b_file)
    if a_token == b_token:
        raise HandoffError("DISTINCT_CREDENTIALS_REQUIRED")
    tls = tls_context(ca_file)
    request = fact(selection)
    started = time.monotonic()
    async with (
        httpx.AsyncClient(
            base_url=selection.origin,
            verify=tls,
            trust_env=False,
            timeout=8,
            headers={"Authorization": "Bearer " + a_token},
        ) as a_http,
        httpx.AsyncClient(
            base_url=selection.origin,
            verify=tls,
            trust_env=False,
            timeout=8,
            headers={"Authorization": "Bearer " + b_token},
        ) as b_http,
    ):
        identities = []
        for http in (a_http, b_http):
            body, _ = await response(http, "GET", "/api/v4/gateway/identity")
            identities.append(GatewayIdentityResponse.model_validate(body))
        if (
            not identities[0].client_id
            or not identities[1].client_id
            or identities[0].client_id == identities[1].client_id
            or identities[0].org_id != identities[1].org_id
        ):
            raise HandoffError("DISTINCT_SAME_ORG_CLIENTS_REQUIRED")
        emit(
            "preflight",
            distinct_clients=True,
            transports=["stdio", "streamable-http"],
            local_tests="operator-confirmed separately; not executed by smoke",
            sources=provenance(),
        )
        async with mcp_session(selection.connection("stdio"), a_http, a_token, ca_file) as a:
            await a.initialize()
            await call(a, "get_platform_capabilities", {})
            await resolved(a, selection.project_id)
            saved = data(await call(a, "save_context", request))["context"]
            validate_saved(saved, request)
            same_record(data(await call(a, "save_context", request))["context"], saved)
            # external_id is create de-duplication, not payload-conflict detection/update.
            changed = {**request, "content": "Fictional retry change; must not replace the fact."}
            same_record(data(await call(a, "save_context", changed))["context"], saved)
            emit(
                "saved",
                context_id=saved["context_id"],
                revision=saved["revision"],
                project_id=saved["project_id"],
                source=saved["source"],
                external_id_replay="same record; changed content did not overwrite",
            )
            memory = data(
                await call(
                    a,
                    "save_memory",
                    {
                        "scope": "project_agent",
                        "project_id": selection.project_id,
                        "type": "handoff-probe",
                        "content": PRIVATE_QUERY + " A",
                        "importance": 0.1,
                        "confidence": 1,
                        "source": "fictional-handoff",
                        "external_id": "handoff-" + selection.run_id + "-private-a",
                    },
                )
            )["memory"]
            # Positive direct read of A's own memory makes absence checks meaningful.
            own, _ = await response(
                a_http,
                "GET",
                "/api/v1/memory/" + memory["memory_id"],
                params={"scope": "project_agent", "project_id": selection.project_id},
            )
            if own.get("content") != PRIVATE_QUERY + " A":
                raise HandoffError("PRIVATE_CONTROL_MISSING")

        # A is closed before B initializes. No chat transcript or answer is sent to B.
        revoked = False
        try:
            async with mcp_session(selection.connection("http"), b_http, b_token, ca_file) as b:
                await b.initialize()
                await call(b, "get_platform_capabilities", {})
                await resolved(b, selection.project_id)
                query = {
                    "query": QUERY,
                    "project_id": selection.project_id,
                    "sources": ["project_context"],
                    "tags": request["tags"],
                    "token_budget": 2000,
                    "limit": 5,
                }
                await indexed(
                    b,
                    "search_knowledge",
                    query,
                    context_key(selection.project_id, ObjectId(saved["context_id"])),
                )
                retrieved = data(
                    await call(
                        b,
                        "get_context",
                        {
                            "project_id": selection.project_id,
                            "context_id": saved["context_id"],
                        },
                    )
                )["context"]
                same_record(retrieved, saved)
                emit(
                    "retrieved",
                    context_id=retrieved["context_id"],
                    revision=retrieved["revision"],
                    content=retrieved["content"],
                    source=retrieved["source"],
                    identity_content_equal=True,
                )
                await resolved(b, selection.negative_project_id)
                negative = data(
                    await call(
                        b,
                        "search_knowledge",
                        {
                            **query,
                            "project_id": selection.negative_project_id,
                        },
                    )
                )
                if any(
                    row.get("key")
                    == context_key(selection.project_id, ObjectId(saved["context_id"]))
                    for row in negative.get("results", [])
                ):
                    raise HandoffError("OTHER_PROJECT_LEAK")
                await refused(
                    b,
                    "get_context",
                    {
                        "project_id": selection.negative_project_id,
                        "context_id": saved["context_id"],
                    },
                    {"NOT_FOUND"},
                )
                b_memory = data(
                    await call(
                        b,
                        "save_memory",
                        {
                            "scope": "project_agent",
                            "project_id": selection.project_id,
                            "type": "handoff-probe",
                            "content": PRIVATE_QUERY + " B",
                            "importance": 0.1,
                            "confidence": 1,
                            "source": "fictional-handoff",
                            "external_id": "handoff-" + selection.run_id + "-private-b",
                        },
                    )
                )["memory"]
                private = await indexed(
                    b,
                    "search_memory",
                    {
                        "query": PRIVATE_QUERY,
                        "scopes": ["project_agent"],
                        "project_id": selection.project_id,
                        "types": ["handoff-probe"],
                        "limit": 5,
                    },
                    # search_memory's MCP formatter uses source:id, while
                    # unified search_knowledge uses the full context_key.
                    f"{MEMORY_SOURCE}:{b_memory['memory_id']}",
                )
                if any(
                    row.get("key") == f"{MEMORY_SOURCE}:{memory['memory_id']}"
                    or row.get("content") == PRIVATE_QUERY + " A"
                    for row in private["results"]
                ):
                    raise HandoffError("PRIVATE_MEMORY_LEAK")
                async with b_http.stream(
                    "GET",
                    "/api/v1/memory/" + memory["memory_id"],
                    params={"scope": "project_agent", "project_id": selection.project_id},
                ) as denial:
                    if denial.status_code != 404:
                        raise HandoffError("PRIVATE_MEMORY_REFUSAL_MISSING")
                emit(
                    "boundaries",
                    permitted_second_project_absent=True,
                    direct_other_project="NOT_FOUND",
                    own_private_control=True,
                    other_client_private_read=404,
                )
                emit("awaiting_recipient_revocation", timeout_seconds=120)
                await wait_for_recipient(gate)
                code = await refused(
                    b,
                    "get_context",
                    {"project_id": selection.project_id, "context_id": saved["context_id"]},
                    {"UNAUTHENTICATED", "FORBIDDEN"},
                )
                revoked = True
                emit("already_connected_revoked", code=code)
        except DiagnosisError as error:
            # Closing an already revoked HTTP session may itself be refused.
            if not revoked or error.code not in {"UNAUTHENTICATED", "FORBIDDEN"}:
                raise
    return {
        "status": "passed",
        "context_id": saved["context_id"],
        "revision": saved["revision"],
        "identity_content_equal": True,
        "active_session_revoked": revoked,
        "seconds": round(time.monotonic() - started, 3),
        "vendor_applications": "not executed",
        "independent_content_review": False,
    }


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:
        raise HandoffError("ARGUMENTS_INVALID")


def main() -> int:
    try:
        parser = Parser(description=__doc__, allow_abbrev=False)
        for name in ("origin", "project-id", "negative-project-id", "run-id"):
            parser.add_argument("--" + name, required=True)
        for name in ("client-a-token-file", "client-b-token-file", "revocation-signal"):
            parser.add_argument("--" + name, required=True, type=Path)
        parser.add_argument("--ca-file", type=Path)
        parser.add_argument("--allow-disposable-writes", action="store_true", required=True)
        parser.add_argument("--confirm-verified-notes", action="store_true", required=True)
        args = parser.parse_args()
        selection = Selection(
            origin=args.origin,
            project_id=args.project_id,
            negative_project_id=args.negative_project_id,
            run_id=args.run_id,
        )

        async def bounded() -> dict[str, Any]:
            async with asyncio.timeout(300):
                return await run(
                    selection,
                    args.client_a_token_file,
                    args.client_b_token_file,
                    args.ca_file,
                    args.revocation_signal,
                )

        report = asyncio.run(bounded())
        emit("complete", **report)
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, HandoffError) else failure_code(error)
        emit(
            "failed",
            status="failed",
            code=code,
            next_step=(
                "Check explicit disposable setup and current grants; "
                "retain TLS and review requirements."
            ),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
