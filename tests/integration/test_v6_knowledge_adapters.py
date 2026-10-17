"""Real session adapters, replica-set writes and mongot candidates with matching IDs."""

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio

from app.identity.browser import SESSION_COOKIE
from app.identity.crypto import csrf
from app.models.common import utc_now
from tests.integration.test_v6_policy_fencing import browser
from tests.v6_knowledge_fixtures import prepare_knowledge

pytestmark = pytest.mark.integration
BASE = "/api/v6/organizations/org-a/knowledge"


@pytest_asyncio.fixture
async def knowledge_db(managed_agents_db):
    return await prepare_knowledge(managed_agents_db)


@asynccontextmanager
async def human_b(h):
    user = await h.db.users.find_one({"_id": "member"})
    membership = await h.db.organization_memberships.find_one(
        {"org_id": "org-b", "user_id": "member"}
    )
    org = await h.db.organizations.find_one({"org_id": "org-b"})
    issued = await h.local.sessions.issue(
        user, membership, org, ["password", "mfa"], utc_now(), utc_now()
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=h.app),
        base_url="https://identity.example",
        cookies={SESSION_COOKIE: issued.token},
    ) as client:
        yield (
            client,
            {
                "Origin": "https://identity.example",
                "X-CSRF-Token": csrf(issued.token),
                "X-Session-Context": h.local.policy_context(issued.row)["context_id"],
            },
        )


async def search_ready(client, headers, base, sources):
    for _ in range(100):
        response = await client.post(
            base + "/search",
            headers=headers,
            json={
                "query": "isolation",
                "project_id": "payment-service",
                "sources": sources,
                "token_budget": 4096,
                "limit": 50,
            },
        )
        assert response.status_code == 200, response.text
        if response.json()["results"]:
            return response
        await asyncio.sleep(0.25)
    pytest.fail("real mongot returned no authorized fixture candidates")


async def test_repeated_id_human_search_skill_versions_and_cursors_are_tenant_bound(knowledge_db):
    h = knowledge_db
    previous_cursor = None
    for context, base, sentinel, foreign in (
        (browser(h, "member"), BASE, "PRIVATE_SENTINEL_A", "PRIVATE_SENTINEL_B"),
        (human_b(h), BASE.replace("org-a", "org-b"), "PRIVATE_SENTINEL_B", "PRIVATE_SENTINEL_A"),
    ):
        async with context as (client, headers):
            found = await search_ready(client, headers, base, ["project_context", "active_skill"])
            assert sentinel in found.text and foreign not in found.text
            assert "PRIVATE_AGENT_SENTINEL" not in found.text
            foreign_rows = await h.db.project_context.find(
                {"org_id": "org-b" if base == BASE else "org-a"}
            ).to_list(None)
            private_rows = await h.db.memories.find(
                {"scope": {"$in": ["agent", "project_agent"]}}
            ).to_list(None)
            assert all(str(row["_id"]) not in found.text for row in (*foreign_rows, *private_rows))
            assert {r["source"] for r in found.json()["results"]} == {
                "project_context",
                "active_skill",
            }
            # Each real candidate reports both search methods, proving the
            # production lexical and vector pipelines were actually exercised.
            diagnostics = found.json()["retrieval"]["diagnostics"]
            assert diagnostics["vector_matches"] > 0 and diagnostics["lexical_matches"] > 0
            skill = await client.get(base + "/skills/retry-skill", headers=headers)
            assert skill.status_code == 200 and sentinel in skill.text and foreign not in skill.text
            assert {v["version"] for v in skill.json()["versions"]} == {"1.0.0", "0.9.0"}
            path = base + "/context?project_id=payment-service&limit=1"
            page = await client.get(path, headers=headers)
            assert page.status_code == 200 and sentinel in page.text
            cursor = page.json()["next_cursor"]
            assert cursor
            second = await client.get(path + "&cursor=" + cursor, headers=headers)
            assert second.status_code == 200 and sentinel in second.text
            wrong_filter = await client.get(
                path + "&status=archived&cursor=" + cursor, headers=headers
            )
            assert wrong_filter.status_code == 422
            if previous_cursor:
                wrong_org = await client.get(path + "&cursor=" + previous_cursor, headers=headers)
                assert wrong_org.status_code == 422 and "PRIVATE_SENTINEL_A" not in wrong_org.text
            previous_cursor = cursor
            other = await h.db.project_context.find_one(
                {"org_id": "org-b" if base == BASE else "org-a"}
            )
            assert (
                await client.get(
                    base + "/context/" + str(other["_id"]) + "?project_id=payment-service",
                    headers=headers,
                )
            ).status_code == 404
            assert (
                await client.get(
                    (base.replace("org-a", "org-b") if base == BASE else BASE) + "/projects",
                    headers=headers,
                )
            ).status_code in {403, 409}
    assert await h.db.retrieval_logs.count_documents({"query": "isolation"}) == 0


async def test_read_a_write_b_and_private_memory_do_not_borrow_authority(knowledge_db):
    h = knowledge_db
    a = await h.db.project_context.find_one({"org_id": "org-a", "project_id": "payment-service"})
    b = await h.db.project_context.find_one({"org_id": "org-a", "project_id": "payments-api"})
    async with browser(h, "member") as (client, headers):
        denied = await client.patch(
            BASE + "/context/" + str(a["_id"]) + "?project_id=payment-service",
            headers=headers,
            json={"expected_revision": a["revision"], "content": "denied"},
        )
        assert denied.status_code == 403
        assert (
            await client.get(BASE + "/context?project_id=payments-api", headers=headers)
        ).status_code == 403
        allowed = await client.patch(
            BASE + "/context/" + str(b["_id"]) + "?project_id=payments-api",
            headers=headers,
            json={"expected_revision": b["revision"], "content": "WRITE_ONLY_SENTINEL isolation"},
        )
        assert allowed.status_code == 200, allowed.text
        for scope, project in (("agent", ""), ("project_agent", "&project_id=payment-service")):
            denied = await client.get(BASE + "/memories?scope=" + scope + project, headers=headers)
            assert denied.status_code == 403
        denied = await client.post(
            BASE + "/search",
            headers=headers,
            json={"query": "isolation", "memory_scopes": ["agent"]},
        )
        assert denied.status_code == 403
        # Search may name write-B but context:read-A prevents selecting it.
        found = await client.post(
            BASE + "/search",
            headers=headers,
            json={
                "query": "isolation",
                "project_id": "payments-api",
                "sources": ["project_context"],
            },
        )
        assert found.status_code == 200 and found.json()["results"] == []
        assert "WRITE_ONLY_SENTINEL" not in found.text
    assert (await h.db.project_context.find_one({"_id": a["_id"]}))["content"] == a["content"]


async def test_real_context_conflict_and_same_scope_memory_supersession_preserve_history(
    knowledge_db,
):
    h = knowledge_db
    a = await h.db.project_context.find_one({"org_id": "org-a", "project_id": "payment-service"})
    async with browser(h) as (client, headers):
        path = BASE + "/context/" + str(a["_id"]) + "?project_id=payment-service"
        body = {"expected_revision": a["revision"], "content": "human revision"}
        saved = await client.patch(path, headers=headers, json=body)
        assert saved.status_code == 200, saved.text
        assert (
            saved.json()["actor"]["actor_type"] == "human"
            and saved.json()["actor"]["user_id"] == "owner"
        )
        assert (await client.patch(path, headers=headers, json=body)).status_code == 409
        memory = {
            "scope": "global",
            "type": "decision",
            "content": "intentional memory",
            "importance": 0.6,
            "confidence": 0.8,
            "source": "human-workspace",
            "external_id": "new-memory",
        }
        created = await client.post(BASE + "/memories", headers=headers, json=memory)
        assert created.status_code == 201, created.text
        old = created.json()
        replacement = await client.post(
            BASE + "/memories",
            headers=headers,
            json={
                **memory,
                "external_id": "replacement",
                "content": "corrected memory",
                "supersedes": old["memory_id"],
            },
        )
        assert replacement.status_code == 201, replacement.text
        previous = await client.get(
            BASE + "/memories/" + old["memory_id"] + "?scope=global", headers=headers
        )
        assert (
            previous.json()["status"] == "superseded"
            and previous.json()["content"] == "intentional memory"
        )
        assert previous.json()["superseded_by"] == replacement.json()["memory_id"]
        foreign = await h.db.memories.find_one({"org_id": "org-b", "scope": "global"})
        assert (
            await client.post(
                BASE + "/memories",
                headers=headers,
                json={
                    **memory,
                    "external_id": "foreign-replacement",
                    "supersedes": str(foreign["_id"]),
                },
            )
        ).status_code == 404
        archived = await client.patch(
            BASE + "/memories/" + replacement.json()["memory_id"] + "?scope=global",
            headers=headers,
            json={"expected_revision": replacement.json()["revision"], "status": "archived"},
        )
        assert archived.status_code == 200 and archived.json()["status"] == "archived"
        # Browser cookies never authenticate legacy paths, even with a header.
        assert (await client.get("/api/v1/projects", headers=headers)).status_code == 401


async def test_verified_machine_and_human_adapters_agree_on_the_same_action_resource_policy(
    knowledge_db,
):
    from datetime import timedelta

    from app.managed_agents.models import ClientApproval, CredentialIssue

    h = knowledge_db
    row = await h.agents.approve(
        "org-a",
        "owner",
        ClientApproval(
            client_id="knowledge-worker",
            name="Fictional knowledge worker",
            client_type="codex",
            subject_id="member",
            parent_id=h.root,
            expected_parent_generation=h.root_generation,
            team_id="alpha",
            expected_team_generation=1,
            tuples=h.knowledge_grants,
            expires_at=utc_now() + timedelta(hours=6),
        ),
    )
    command = CredentialIssue(
        issuance_id="knowledge-key",
        expected_client_revision=row["revision"],
        tuples=h.knowledge_grants,
        expires_at=utc_now() + timedelta(hours=1),
    )
    async with browser(h, "member") as (human, human_headers):
        delivered = await human.post(
            "/api/v6/organizations/org-a/managed-clients/knowledge-worker/credentials",
            headers=human_headers,
            json=command.model_dump(mode="json"),
        )
        assert delivered.status_code == 200, delivered.text
        token = delivered.json()["token"]
        # Separate transport with no browser cookies. The token is a normal
        # bounded recipient-issued credential, never an API administrator key.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=h.app),
            base_url="https://identity.example",
            headers={"Authorization": "Bearer " + token},
        ) as machine:
            for human_path, machine_path in (
                ("/projects", "/projects"),
                ("/context?project_id=payment-service", "/context?project_id=payment-service"),
                ("/skills/retry-skill", "/skills/retry-skill"),
                ("/memories?scope=global", "/memory?scope=global"),
            ):
                a = await human.get(BASE + human_path, headers=human_headers)
                b = await machine.get("/api/v1" + machine_path)
                assert a.status_code == b.status_code == 200, (a.text, b.text)
                assert a.json() == b.json()
            for client, prefix, headers in ((human, BASE, human_headers), (machine, "/api/v1", {})):
                assert (
                    await client.get(prefix + "/context?project_id=payments-api", headers=headers)
                ).status_code == 403
                a = await h.db.project_context.find_one(
                    {"org_id": "org-a", "project_id": "payment-service"}
                )
                denied = await client.patch(
                    prefix + "/context/" + str(a["_id"]) + "?project_id=payment-service",
                    headers=headers,
                    json={"expected_revision": a["revision"], "content": "must not borrow write-B"},
                )
                assert denied.status_code == 403
