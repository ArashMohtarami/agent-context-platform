"""Already-connected real REST, stdio and HTTP MCP lose current authority."""

import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from v6_managed_agent_fixtures import approval, provision

from app.management.memberships import Memberships
from app.management.models import TeamChange
from app.management.teams import Teams
from app.mcp.auth import Authorization
from app.mcp.remote_http import RemoteMcpGateway, add_remote_mcp_route
from app.models.common import utc_now
from app.models.job_authority import JobAuthority
from app.security.job_authority import JobAuthorityError, MongoJobAuthorityChecker
from app.security.policy.engine import PolicyEngine
from app.security.tenant import TenantContext, tenant_scope
from tests.integration.test_v6_mcp_isolation import application, listening

pytestmark = pytest.mark.integration


async def revoke(h, kind, row, delivered):
    if kind == "sponsor":
        member = await h.db.organization_memberships.find_one(
            {"org_id": "org-a", "user_id": "member"}
        )
        await Memberships(h.engine).change_status(
            "org-a", "owner", "member", member["revision"], "suspended"
        )
    elif kind == "client":
        await h.agents.revoke("org-a", "lead", row["client_id"], row["revision"])
    elif kind == "team":
        await Teams(h.engine).change_team(
            "org-a", "owner", "alpha", TeamChange(expected_revision=1, status="suspended")
        )
    elif kind == "parent":
        await h.governance.change("org-a", "owner", h.lead["_id"], 1, revoke=True)
    else:
        await h.agents.revoke("org-a", "lead", row["client_id"], 1, key_id=delivered["key_id"])


@pytest.mark.parametrize("kind", ["sponsor", "client", "key", "team", "parent"])
async def test_real_rest_current_sponsor_client_key_team_parent_revocation(managed_agents_db, kind):
    h = managed_agents_db
    row, _, delivered = await provision(h)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + delivered["token"]},
    ) as client:
        assert (await client.get("/api/v1/skills/retry-skill")).status_code == 200
        await revoke(h, kind, row, delivered)
        response = await client.get("/api/v1/skills/retry-skill")
        assert response.status_code in (401, 403)
        assert "PRIVATE_SENTINEL" not in response.text


@pytest.mark.parametrize("kind", ["sponsor", "client", "key"])
async def test_actual_stdio_connected_session_rechecks_revocation(
    managed_agents_db, tmp_path, kind
):
    h = managed_agents_db
    row, _, delivered = await provision(h)
    async with listening(h.app) as url:
        credential = tmp_path / "token"
        credential.write_text(delivered["token"])
        credential.chmod(0o600)
        parameters = StdioServerParameters(
            command=sys.executable,
            cwd=str(tmp_path),
            args=[
                "-c",
                "import asyncio; from app.mcp.server import run_stdio; asyncio.run(run_stdio())",
            ],
            env={
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                "MCP_API_BASE_URL": url,
                "MCP_API_TOKEN_FILE": str(credential),
                "MCP_REQUEST_TIMEOUT_SECONDS": "15",
            },
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_skill", {"skill_id": "retry-skill"})
                assert not result.isError, str(result)
                assert "PRIVATE_SENTINEL_A" in str(result)
                await revoke(h, kind, row, delivered)
                refused = await session.call_tool("get_skill", {"skill_id": "retry-skill"})
                assert refused.isError and "PRIVATE_SENTINEL" not in str(refused)


@pytest.mark.parametrize("kind", ["sponsor", "client", "key"])
async def test_active_http_mcp_session_rechecks_revocation(managed_agents_db, kind):
    h = managed_agents_db
    row, _, delivered = await provision(h)
    app = application(h.db)
    app.state.auth = h.app.state.auth
    app.state.settings = h.app.state.settings
    remote = RemoteMcpGateway()
    add_remote_mcp_route(app, remote)
    async with remote.run(app, authorization=Authorization(), server_name="agent-context-platform"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://identity.example"
        ) as http:
            headers = {
                "Authorization": "Bearer " + delivered["token"],
                "Accept": "application/json, text/event-stream",
            }
            response = await http.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "phase7", "version": "1"},
                    },
                },
            )
            assert response.status_code == 200, response.text
            headers.update(
                {
                    "Mcp-Session-Id": response.headers["mcp-session-id"],
                    "Mcp-Protocol-Version": "2025-06-18",
                }
            )
            body = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_skill", "arguments": {"skill_id": "retry-skill"}},
            }
            first = await http.post("/mcp", headers=headers, json=body)
            assert first.status_code == 200 and "PRIVATE_SENTINEL_A" in first.text, first.text
            await revoke(h, kind, row, delivered)
            refused = await http.post("/mcp", headers=headers, json={**body, "id": 3})
            assert refused.status_code in (200, 401, 403, 404)
            assert "PRIVATE_SENTINEL" not in refused.text
            assert refused.status_code != 200 or '"isError":true' in refused.text.replace(" ", "")


async def test_pending_machine_job_recheck_denies_result_without_undoing_committed_work(
    managed_agents_db,
):
    h = managed_agents_db
    row, _, delivered = await provision(h)
    principal = await h.app.state.auth.authenticate(delivered["token"])
    auth = JobAuthority(
        org_id="org-a",
        key_id=delivered["key_id"],
        client_id=row["client_id"],
        worker_schema_version=42,
        subject_kind="machine",
        sponsor_id="member",
        client_policy_revision=row["revision"],
        policy_context=principal.managed_policy.fingerprint,
        project_ids=tuple(h.source["authorization"]["project_ids"]),
        profile_ids=None,
        research_project_id=h.source["research_project_id"],
        source_id=h.source["source_id"],
    )
    checker = MongoJobAuthorityChecker(h.db, worker_schema_version=42)
    before = await h.db.research_documents.count_documents({"org_id": "org-a"})
    job = await h.db.ingestion_jobs.find_one({"org_id": "org-a"})
    await h.db.ingestion_jobs.update_one(
        {"_id": job["_id"]}, {"$set": {"initiating_authority": auth.model_dump(mode="python")}}
    )
    path = "/api/v3/research/sources/" + job["source_id"] + "/ingestion-jobs/" + job["job_id"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + delivered["token"]},
    ) as http:
        response = await http.get(path)
        assert response.status_code == 200, response.text
    with tenant_scope(TenantContext("org-a")):
        await checker.check(auth)
        await revoke(h, "sponsor", row, delivered)
        with pytest.raises(JobAuthorityError):
            await checker.check(auth)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + delivered["token"]},
    ) as http:
        denied = await http.get(path)
        assert denied.status_code in (401, 403, 404)
        assert "PRIVATE_SENTINEL" not in denied.text
    assert await h.db.research_documents.count_documents({"org_id": "org-a"}) == before


async def test_unclaimed_legacy_keys_and_schema41_workers_are_refused(managed_agents_db):
    from app.managed_agents.models import LegacyClaim
    from app.security.policy.evaluation import PolicyDenied

    h = managed_agents_db
    legacy = next(k for k in h.legacy_keys if k["org_id"] == "org-a")
    await h.db.api_keys.update_one({"_id": legacy["_id"]}, {"$set": {"status": "active"}})
    await h.db["_managed_agents_control"].update_one({}, {"$set": {"activated": False}})
    with tenant_scope(TenantContext("org-a")):
        with pytest.raises(PolicyDenied):
            await h.cutover.activate("org-a", "fictional-incomplete-inventory")
        # Descriptive owners are never trusted. Claim needs an approved real sponsor.
        old_client = await h.db.agent_clients.find_one(
            {"org_id": "org-a", "client_id": legacy["client_id"] or "agent-one"}
        )
        command = approval(h).model_copy(
            update={
                "client_id": old_client["client_id"],
                "expected_revision": old_client["revision"],
            }
        )
        reviewed = await h.agents.approve("org-a", "lead", command)
        claimed = await h.cutover.claim(
            "org-a",
            LegacyClaim(
                key_id=legacy["key_id"],
                client_id=old_client["client_id"],
                expected_client_revision=reviewed["revision"],
                tuples=tuple(g for g in h.domain if g.action in legacy["scopes"]),
                expires_at=utc_now() + timedelta(hours=1),
            ),
            "fictional-exact-verified-key-claim",
        )
        assert claimed["recipient_id"] == "member"
        await h.cutover.activate("org-a", "fictional-complete-inventory")
        from app.db.migrations.v042_managed_agents import ManagedAgentsMigration

        preserved = await h.db.api_keys.find_one({"_id": legacy["_id"]})
        receipts = await h.db.managed_credentials.find({}).to_list(100)
        await ManagedAgentsMigration().up(h.db)
        assert await h.db.api_keys.find_one({"_id": legacy["_id"]}) == preserved
        assert await h.db.managed_credentials.find({}).to_list(100) == receipts
        assert (await h.db["_managed_agents_control"].find_one({}))["activated"]
    with pytest.raises(PolicyDenied):
        await PolicyEngine(h.store, human_management=True).enabled()


async def test_real_action_resource_intersection_and_foreign_client_namespace(
    managed_agents_db, tmp_path
):
    from v6_managed_agent_fixtures import g

    from app.managed_agents.models import CredentialIssue

    h = managed_agents_db
    tuples = (g("context:read"), g("context:write", ids=("payments-api",)))
    row = await h.agents.approve("org-a", "lead", approval(h).model_copy(update={"tuples": tuples}))
    delivered = await h.agents.issue(
        "org-a",
        "member",
        row["client_id"],
        CredentialIssue(
            issuance_id="coupled-actions",
            expected_client_revision=1,
            tuples=tuples,
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )
    await h.db.managed_clients.insert_one(
        {
            **row,
            "_id": "foreign-" + row["_id"],
            "org_id": "org-b",
            "sponsor_id": "FOREIGN_PRIVATE_SPONSOR",
        }
    )
    assert "FOREIGN_PRIVATE_SPONSOR" not in str(
        await h.agents.inspect("org-a", "lead", row["client_id"])
    )
    payload = {
        "project_id": "payments-api",
        "type": "convention",
        "title": "Fictional safe context",
        "content": "Fictional context for Phase7 isolation",
        "importance": 0.5,
        "source": "phase7-test",
        "external_id": "same-logical-external-id",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + delivered["token"]},
    ) as http:
        read_a = await http.get("/api/v1/context", params={"project_id": "payment-service"})
        assert read_a.status_code == 200, read_a.text
        write_a = await http.post(
            "/api/v1/context", json={**payload, "project_id": "payment-service"}
        )
        assert write_a.status_code == 403, write_a.text
        write_b = await http.post("/api/v1/context", json=payload)
        assert write_b.status_code == 201, write_b.text
        assert (
            await http.get("/api/v1/context", params={"project_id": "payments-api"})
        ).status_code == 403
    async with listening(h.app) as url:
        credential = tmp_path / "coupled-token"
        credential.write_text(delivered["token"])
        credential.chmod(0o600)
        parameters = StdioServerParameters(
            command=sys.executable,
            cwd=str(tmp_path),
            args=[
                "-c",
                "import asyncio; from app.mcp.server import run_stdio; asyncio.run(run_stdio())",
            ],
            env={
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                "MCP_API_BASE_URL": url,
                "MCP_API_TOKEN_FILE": str(credential),
                "MCP_REQUEST_TIMEOUT_SECONDS": "15",
            },
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                denied = await session.call_tool(
                    "save_context", {**payload, "project_id": "payment-service"}
                )
                assert denied.isError
                permitted = await session.call_tool(
                    "save_context",
                    {
                        **payload,
                        "external_id": "second-logical-external-id",
                        "idempotency_key": "second-logical-external-id",
                    },
                )
                assert not permitted.isError, str(permitted)
                replayed = await session.call_tool(
                    "save_context",
                    {
                        **payload,
                        "external_id": "second-logical-external-id",
                        "idempotency_key": "second-logical-external-id",
                    },
                )
                assert not replayed.isError, str(replayed)
                assert (
                    await h.db.gateway_idempotency.count_documents(
                        {"org_id": "org-a", "client_id": "managed-worker"}
                    )
                    == 1
                )


async def test_project_directory_only_grant_works_without_corpus_permission(managed_agents_db):
    from v6_managed_agent_fixtures import g

    from app.managed_agents.models import CredentialIssue

    h = managed_agents_db
    tuples = (g("projects:read", "project_directory"),)
    row = await h.agents.approve("org-a", "lead", approval(h).model_copy(update={"tuples": tuples}))
    delivered = await h.agents.issue(
        "org-a",
        "member",
        row["client_id"],
        CredentialIssue(
            issuance_id="directory-only",
            expected_client_revision=1,
            tuples=tuples,
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )
    key = await h.db.api_keys.find_one({"key_id": delivered["key_id"]})
    assert key["project_mode"] == "include" and key["projects"] == ["payment-service"]
    assert key["profile_mode"] == "none" and key["profiles"] == []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + delivered["token"]},
    ) as http:
        projects = await http.get("/api/v1/projects")
        assert projects.status_code == 200, projects.text
        assert [r["project_id"] for r in projects.json()["items"]] == ["payment-service"]
        assert (
            await http.get("/api/v1/context", params={"project_id": "payment-service"})
        ).status_code == 403
