"""Distributed demo against real storage/TLS/MCP; fixture setup is not an installer."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import uvicorn
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db.tenant_collection import TenantCollection
from app.identity.models import MANAGEMENT_ACTIONS
from app.managed_agents.models import ClientApproval
from app.management.memberships import Memberships
from app.management.models import RootApproval
from app.mcp.auth import Authorization
from app.mcp.remote_http import RemoteMcpRoute
from app.mcp.remote_security import RemoteMcpTransportPolicy
from app.models.common import utc_now
from app.security.policy.models import Grant, Issue, Selector
from app.security.tenant import TenantContext, tenant_scope
from app.security.tenant_policy import ResourceGrant, TenantPolicy, policy_scope
from tests.integration.test_v6_knowledge_adapters import knowledge_db as knowledge_db
from tests.integration.test_v6_policy_fencing import browser
from tests.v6_managed_agent_fixtures import g

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


async def clients(h):
    """Explicit fictional operator review, independent owner approval, recipient issue."""
    root = await h.db.policy_assignments.find_one({"_id": h.root})
    expiry = utc_now() + timedelta(hours=1)
    base = (
        g("projects:read", "project_directory", ("payment-service", "payments-api")),
        g("context:read", ids=("payment-service", "payments-api")),
        g("agent:self", "organization_knowledge", ("org-a",)),
    )
    # Create metadata first: included client_memory references must really exist.
    for client in ("handoff-a", "handoff-b"):
        await h.agents.approve(
            "org-a",
            "owner",
            ClientApproval(
                client_id=client,
                name="Fictional " + client,
                subject_id="member",
                parent_id=h.root,
                expected_parent_generation=root["generation"],
                tuples=base,
                expires_at=expiry,
            ),
        )
    domain = (
        *base,
        g("context:write"),
        g("search:read", ids=("payment-service", "payments-api")),
        g("memory:read"),
        g("memory:write"),
        g("memory:read", "client_memory", ("handoff-a", "handoff-b")),
        g("memory:write", "client_memory", ("handoff-a", "handoff-b")),
    )
    own = tuple(
        g(a, "member", ("member",))
        for a in ("credentials:issue:own", "credentials:revoke:own", "clients:read:own")
    )
    with tenant_scope(TenantContext("org-a")):
        updated = await Memberships(h.engine).operator_approve_root(
            "org-a",
            "owner",
            RootApproval(
                tuples=tuple(
                    Grant.model_validate(x)
                    for x in root["tuples"]
                    if x["action"] in MANAGEMENT_ACTIONS
                ),
                ceiling=tuple(Grant.model_validate(x) for x in root["ceiling"]) + domain + own,
                targets=Selector(mode="include", ids=("member",)),
                allowed_roles=("contributor",),
                expires_at=utc_now() + timedelta(hours=2),
            ),
            "fictional-handoff-explicit-private-memory-review",
        )
    await h.governance.issue(
        "org-a",
        "owner",
        Issue(
            subject_id="member",
            parent_id=h.root,
            expected_parent_generation=updated["generation"],
            tuples=domain + own,
            expires_at=expiry,
        ),
    )
    approvals = []
    for client in ("handoff-a", "handoff-b"):
        selected = tuple(
            g(x.action, "client_memory", (client,)) if x.resource_kind == "client_memory" else x
            for x in domain
            if not (client == "handoff-b" and x.action == "context:write")
        )
        approved = await h.agents.approve(
            "org-a",
            "owner",
            ClientApproval(
                client_id=client,
                expected_revision=1,
                name="Fictional " + client,
                subject_id="member",
                parent_id=h.root,
                expected_parent_generation=updated["generation"],
                tuples=selected,
                expires_at=expiry,
            ),
        )
        approvals.append((client, selected, approved))
    receipts = []
    for client, selected, approved in approvals:
        async with browser(h, "member") as (human, headers):
            inspected = await human.get(
                "/api/v6/organizations/org-a/managed-clients/" + client, headers=headers
            )
            assert inspected.status_code == 200
            current = inspected.json()["client"]
            assert current["revision"] == approved["revision"]
            issued = await human.post(
                "/api/v6/organizations/org-a/managed-clients/" + client + "/credentials",
                headers=headers,
                json={
                    "issuance_id": "fictional-handoff-once",
                    "expected_client_revision": current["revision"],
                    "tuples": [x.model_dump(mode="json") for x in selected],
                    # Use the persisted bound, not a pre-BSON microsecond value.
                    "expires_at": current["expires_at"],
                },
            )
            assert issued.status_code == 200, issued.json()
            receipt = issued.json()
            assert receipt["delivered"] and receipt["token"]
            receipts.append(receipt)
    return receipts


async def test_real_protocol_handoff_and_boundaries(knowledge_db, tmp_path):  # noqa: F811 -- imported real fixture
    h = knowledge_db
    h.app.state.embeddings = None  # Real lexical mongot only; no provider/model-quality claim.
    # Observe only this fictional fixture's 422 reason; preserve the real handler
    # and response. Never export headers, tokens, input dictionaries or live data.
    original = h.app.exception_handlers[StarletteHTTPException]

    async def observed(request, error):
        if request.url.path == "/api/v1/memory" and error.status_code == 422:
            target = Path(os.environ["ACP_FOCUSED_HANDOFF_REPORT"]) / "memory-validation.json"
            target.write_text(
                json.dumps({"status": 422, "detail": str(error.detail)[:2000]}) + "\n"
            )
        return await original(request, error)

    h.app.add_exception_handler(StarletteHTTPException, observed)
    issued = await clients(h)
    tmp_path.chmod(0o700)
    for name, receipt in zip(("a", "b"), issued, strict=True):
        path = tmp_path / name
        path.write_text(receipt["token"])
        path.chmod(0o600)
    cert, key = tmp_path / "ca.crt", tmp_path / "server.key"
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "/usr/bin/openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=fictional-handoff",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, "scoped disposable certificate generation failed"
    remote = next(route.endpoint for route in h.app.routes if isinstance(route, RemoteMcpRoute))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = "https://127.0.0.1:" + str(sock.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            h.app,
            log_level="error",
            access_log=False,
            lifespan="off",
            ssl_keyfile=str(key),
            ssl_certfile=str(cert),
        )
    )
    argv = [
        sys.executable,
        "-I",
        str(ROOT / "examples/agent-handoff/smoke.py"),
        "--origin",
        origin,
        "--project-id",
        "payment-service",
        "--negative-project-id",
        "payments-api",
        "--run-id",
        "fictional-focused",
        "--client-a-token-file",
        str(tmp_path / "a"),
        "--client-b-token-file",
        str(tmp_path / "b"),
        "--ca-file",
        str(cert),
        "--revocation-signal",
        str(tmp_path / "revoked"),
        "--allow-disposable-writes",
        "--confirm-verified-notes",
    ]
    events = []
    async with remote.run(
        h.app,
        authorization=Authorization(),
        server_name="agent-context-platform",
        transport_policy=RemoteMcpTransportPolicy(allow_loopback_http=False),
    ):
        task = asyncio.create_task(server.serve(sockets=[sock]))
        child = None
        try:
            for _ in range(200):
                if server.started:
                    break
                await asyncio.sleep(0.02)
            assert server.started
            child = await asyncio.create_subprocess_exec(
                *argv,
                cwd=ROOT,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            async with asyncio.timeout(330):
                while line := await child.stdout.readline():
                    event = json.loads(line)
                    events.append(event)
                    report = Path(os.environ["ACP_FOCUSED_HANDOFF_REPORT"])
                    (report / "handoff-checkpoints.json").write_text(
                        json.dumps(events, indent=2) + "\n"
                    )
                    assert event["checkpoint"] != "failed", events
                    if event["checkpoint"] == "awaiting_recipient_revocation":
                        # Existing recipient session/API, never a machine admin scope.
                        async with browser(h, "member") as (human, headers):
                            denied = await human.post(
                                "/api/v6/organizations/org-a/managed-clients/handoff-b/credentials/"
                                + issued[1]["key_id"]
                                + "/revoke",
                                headers=headers,
                                json={"expected_revision": issued[1]["revision"]},
                            )
                            assert denied.status_code == 200, denied.status_code
                        (tmp_path / "revoked").write_text("revoked\n")
                        (tmp_path / "revoked").chmod(0o600)
                assert await child.wait() == 0
            assert events[-1]["status"] == "passed" and events[-1]["active_session_revoked"]
            saved = next(row for row in events if row["checkpoint"] == "saved")
            read = next(row for row in events if row["checkpoint"] == "retrieved")
            assert (
                saved["context_id"] == read["context_id"]
                and saved["revision"] == read["revision"] == 1
            )
            stored = await h.db.project_context.find_one(
                {"external_id": "handoff-fictional-focused-fact-v1"}
            )
            assert stored["content"] == read["content"]
            assert stored["actor"]["client_id"] == "handoff-a"
            assert (
                await h.db.project_context.count_documents({"external_id": stored["external_id"]})
                == 1
            )
            serialized = json.dumps(events)
            assert all(receipt["token"] not in serialized for receipt in issued)
            assert all(
                value not in serialized for value in (str(tmp_path), origin, "member@example.test")
            )
            report = Path(os.environ["ACP_FOCUSED_HANDOFF_REPORT"])
            (report / "handoff-checkpoints.json").write_text(json.dumps(events, indent=2) + "\n")
        finally:
            if child and child.returncode is None:
                child.terminate()
                try:
                    await asyncio.wait_for(child.wait(), 15)
                except TimeoutError:
                    child.kill()
                    await child.wait()
            server.should_exit = True
            await asyncio.wait_for(task, 15)
            sock.close()
            for name in ("a", "b", "server.key", "revoked"):
                (tmp_path / name).unlink(missing_ok=True)


async def test_private_memory_reference_checks_remain_action_owner_and_tenant_bound(knowledge_db):  # noqa: F811 -- real fixture
    h = knowledge_db
    await clients(h)
    adapter = TenantCollection(h.db["memories"], "memories", lambda: TenantContext("org-a"))
    own = TenantPolicy(
        "org-a",
        "fictional-key",
        (
            ResourceGrant("memory:write", "project", "include", ("payment-service",)),
            ResourceGrant("memory:write", "client_memory", "include", ("handoff-a",)),
        ),
        client_id="handoff-a",
        managed=True,
        subject_kind="machine",
    )
    document = {
        "org_id": "org-a",
        "scope": "project_agent",
        "project_id": "payment-service",
        "client_id": "handoff-a",
    }
    with tenant_scope(TenantContext("org-a")), policy_scope(own, "memory:write"):
        await adapter._references(document)
    from dataclasses import replace

    # Real bounded queries, not mocked authorization/storage outcomes.
    for policy, action, candidate in (
        (own, "memory:write", {**document, "client_id": "handoff-b"}),
        (own, "memory:write", {**document, "org_id": "org-b"}),
        (own, "memory:write", {**document, "project_id": "payments-api"}),
        (replace(own, client_id=None, subject_kind="human"), "memory:write", document),
        (replace(own, grants=own.grants[:1]), "memory:write", document),
        (
            replace(
                own, grants=tuple(replace(grant, action="memory:read") for grant in own.grants)
            ),
            "memory:read",
            document,
        ),
    ):
        with tenant_scope(TenantContext("org-a")), policy_scope(policy, action):
            with pytest.raises(ValueError, match="reference"):
                await adapter._references(candidate)
    await h.agents.revoke("org-a", "owner", "handoff-a", 2)
    with tenant_scope(TenantContext("org-a")), policy_scope(own, "memory:write"):
        with pytest.raises(ValueError, match="reference"):
            await adapter._references(document)
