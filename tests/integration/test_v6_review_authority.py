"""Real V2/V6 and connected MCP cannot turn writing or a second client into approval."""

import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.managed_agents.models import CredentialIssue
from app.mcp.auth import Authorization
from app.mcp.remote_http import RemoteMcpGateway, add_remote_mcp_route
from app.models.common import utc_now
from app.security.policy.models import Grant
from tests.integration.test_v6_mcp_isolation import application, listening
from tests.integration.test_v6_policy_fencing import browser
from tests.integration.test_v6_service_account_reviews import service_worker
from tests.v6_managed_agent_fixtures import approval, provision

pytestmark = pytest.mark.integration
PROFILE = "personal-brand"
PATH = "/api/v2/content/creator-authority-draft"


async def authored_v2(h, token):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + token},
    ) as http:
        draft = await http.post(
            "/api/v2/content",
            json={
                "profile_id": PROFILE,
                "content_id": "creator-authority-draft",
                "platform": "linkedin",
                "content_type": "post",
                "title": "Fictional public guidance",
                "body": "Public guidance is supplied by the calling author.",
                "project_ids": ["payment-service"],
            },
        )
        assert draft.status_code == 201, draft.text
        reviewed = await http.patch(
            PATH, json={"profile_id": PROFILE, "expected_revision": 1, "status": "review"}
        )
        assert reviewed.status_code == 200, reviewed.text
        assert (
            await http.post(PATH + "/approve", json={"profile_id": PROFILE, "expected_revision": 2})
        ).status_code == 403
    return reviewed.json()


async def test_separately_approved_service_second_client_has_same_sponsor_and_cannot_self_review(
    managed_agents_db,
):
    h = managed_agents_db
    worker, delivered = await service_worker(h)
    authored = await authored_v2(h, delivered["token"])
    assert authored["actor"]["sponsor_id"] == "member"
    sibling = await h.agents.approve(
        "org-a",
        "lead",
        approval(h).model_copy(
            update={
                "client_id": "sibling-review-client",
                "subject_kind": "service_account",
                "subject_id": "fictional-ci",
                "tuples": tuple(Grant.model_validate(x) for x in worker["tuples"]),
            }
        ),
    )
    key = await h.agents.issue(
        "org-a",
        "member",
        sibling["client_id"],
        CredentialIssue(
            issuance_id="sibling-delivery",
            expected_client_revision=sibling["revision"],
            tuples=tuple(Grant.model_validate(x) for x in sibling["tuples"]),
            expires_at=utc_now() + timedelta(hours=1),
        ),
    )
    principal = await h.app.state.auth.authenticate(key["token"])
    assert principal.managed_policy.reviewer and principal.managed_policy.sponsor_ids == ("member",)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + key["token"]},
    ) as http:
        assert (
            await http.post(PATH + "/approve", json={"profile_id": PROFILE, "expected_revision": 2})
        ).status_code == 403
    h.app.state.settings = h.app.state.settings.model_copy(update={"v6_console_enabled": True})
    async with browser(h, "member") as (http, headers):
        own = await http.post(
            "/api/v6/organizations/org-a/creator/items",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "content_id": "human-sponsor-draft",
                "platform": "linkedin",
                "content_type": "post",
                "title": "Human sponsor prose",
                "body": "A human sponsor supplies this public prose.",
                "project_ids": ["payment-service"],
            },
        )
        assert own.status_code == 201, own.text
        assert (
            await http.patch(
                "/api/v6/organizations/org-a/creator/items/human-sponsor-draft",
                headers=headers,
                json={"profile_id": PROFILE, "expected_revision": 1, "status": "review"},
            )
        ).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + key["token"]},
    ) as http:
        refused = await http.post(
            "/api/v2/content/human-sponsor-draft/approve",
            json={"profile_id": PROFILE, "expected_revision": 2},
        )
        assert refused.status_code == 403, refused.text
    async with browser(h, "reviewer") as (http, headers):
        response = await http.post(
            "/api/v6/organizations/org-a/creator/items",
            headers=headers,
            json={
                "profile_id": PROFILE,
                "content_id": "independent-human-draft",
                "platform": "linkedin",
                "content_type": "post",
                "title": "Independent public guidance",
                "body": "Explicit human-authored public tutorial.",
                "project_ids": ["payment-service"],
            },
        )
        assert response.status_code == 201, response.text
        assert (
            await http.patch(
                "/api/v6/organizations/org-a/creator/items/independent-human-draft",
                headers=headers,
                json={"profile_id": PROFILE, "expected_revision": 1, "status": "review"},
            )
        ).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        headers={"Authorization": "Bearer " + key["token"]},
    ) as http:
        approved = await http.post(
            "/api/v2/content/independent-human-draft/approve",
            json={"profile_id": PROFILE, "expected_revision": 2},
        )
        assert approved.status_code == 200 and approved.json()["content"]["status"] == "approved", (
            approved.text
        )
        edited = await http.patch(
            "/api/v2/content/independent-human-draft",
            json={"profile_id": PROFILE, "expected_revision": 3, "title": "Revised public title"},
        )
        assert edited.status_code == 200 and edited.json()["status"] == "drafting", edited.text


@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_real_mcp_safety_scan_is_advisory_and_cannot_approve_or_publish(
    managed_agents_db, tmp_path, transport
):
    h = managed_agents_db
    _, _, delivered = await provision(h)
    await authored_v2(h, delivered["token"])
    args = {"profile_id": PROFILE, "content_id": "creator-authority-draft"}
    if transport == "stdio":
        async with listening(h.app) as url:
            token_file = tmp_path / "synthetic-key"
            token_file.write_text(delivered["token"])
            token_file.chmod(0o600)
            params = StdioServerParameters(
                command=sys.executable,
                cwd=str(tmp_path),
                args=[
                    "-c",
                    "import asyncio; from app.mcp.server import run_stdio; "
                    "asyncio.run(run_stdio())",
                ],
                env={
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                    "MCP_API_BASE_URL": url,
                    "MCP_API_TOKEN_FILE": str(token_file),
                    "MCP_REQUEST_TIMEOUT_SECONDS": "15",
                },
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    assert not (
                        await session.call_tool("review_content_for_publication", args)
                    ).isError
                    assert (
                        await session.call_tool(
                            "review_content_for_publication", {**args, "status": "approved"}
                        )
                    ).isError
                    assert (
                        await session.call_tool(
                            "mark_content_published", {**args, "expected_revision": 2}
                        )
                    ).isError
                    assert not any(
                        t.name == "approve_content" for t in (await session.list_tools()).tools
                    )
    else:
        app = application(h.db)
        app.state.auth = h.app.state.auth
        app.state.settings = h.app.state.settings
        remote = RemoteMcpGateway()
        add_remote_mcp_route(app, remote)
        async with remote.run(
            app, authorization=Authorization(), server_name="agent-context-platform"
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://identity.example"
            ) as http:
                headers = {
                    "Authorization": "Bearer " + delivered["token"],
                    "Accept": "application/json, text/event-stream",
                }
                initialize = await http.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "phase13", "version": "1"},
                        },
                    },
                )
                assert initialize.status_code == 200, initialize.text
                headers.update(
                    {
                        "Mcp-Session-Id": initialize.headers["mcp-session-id"],
                        "Mcp-Protocol-Version": "2025-06-18",
                    }
                )
                for number, (name, arguments, deny) in enumerate(
                    (
                        ("review_content_for_publication", args, False),
                        ("review_content_for_publication", {**args, "status": "approved"}, True),
                        ("mark_content_published", {**args, "expected_revision": 2}, True),
                    ),
                    2,
                ):
                    result = await http.post(
                        "/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": number,
                            "method": "tools/call",
                            "params": {"name": name, "arguments": arguments},
                        },
                    )
                    assert result.status_code == 200, result.text
                    assert ('"isError":true' in result.text.replace(" ", "")) is deny, result.text
    row = await h.db.content_items.find_one(
        {"content_id": "creator-authority-draft", "org_id": "org-a"}
    )
    assert row["status"] == "review" and row["published_at"] is None
