"""Actual replica-set console adapters: boundaries, previews, copies and revisions."""

from datetime import timedelta

import pytest
from v6_managed_agent_fixtures import approval

from app.managed_agents.models import TemplatePut
from app.models.common import utc_now
from app.security.policy.models import Issue
from tests.integration.test_v6_policy_fencing import browser

pytestmark = pytest.mark.integration
BASE = "/api/v6/organizations/org-a/management"


async def test_context_projection_preview_is_nonmutating_and_denies_foreign_self(managed_agents_db):
    h = managed_agents_db
    async with browser(h, "owner") as (client, headers):
        context = await client.get(BASE + "/context", headers=headers)
        assert context.status_code == 200, context.text
        assert all(p["_id"] == h.root for p in context.json()["parents"])
        before = await h.db.policy_assignments.count_documents({})
        anchor = await h.engine.anchor("org-a")
        command = Issue(
            subject_id="member",
            parent_id=h.root,
            expected_parent_generation=3,
            tuples=(),
            expires_at=utc_now() + timedelta(hours=1),
        )
        preview = await client.post(
            BASE + "/policy-preview", headers=headers, json=command.model_dump(mode="json")
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["tuples"] == []
        assert preview.json()["existing_assignments_changed"] is False
        assert await h.db.policy_assignments.count_documents({}) == before
        assert await h.engine.anchor("org-a") == anchor
        for target in ("owner", "foreign-user"):
            denied = await client.post(
                BASE + "/policy-preview",
                headers=headers,
                json=command.model_copy(update={"subject_id": target}).model_dump(mode="json"),
            )
            assert denied.status_code == 403
        stale = await client.post(
            BASE + "/policy-preview",
            headers=headers,
            json=command.model_copy(update={"expected_subject_revision": 999}).model_dump(
                mode="json"
            ),
        )
        assert stale.status_code == 409
        stale_issue = await client.post(
            "/api/v6/organizations/org-a/delegations",
            headers=headers,
            json=command.model_copy(update={"expected_subject_revision": 999}).model_dump(
                mode="json"
            ),
        )
        assert stale_issue.status_code == 409
        assert await h.db.policy_assignments.count_documents({}) == before
        assert await h.engine.anchor("org-a") == anchor
        foreign = await client.get(BASE.replace("org-a", "org-b") + "/context", headers=headers)
        assert foreign.status_code in {403, 409}


async def test_template_impact_real_count_copy_and_revision_denial(managed_agents_db):
    h = managed_agents_db
    command = TemplatePut(
        expected_revision=0, name="Fictional read template", scopes=("context:read",)
    )
    await h.agents.put_template("org-a", "owner", "console-template", command)
    approved = approval(h).model_copy(
        update={
            "profile_id": "console-template",
            "expected_profile_revision": 1,
            "tuples": tuple(g for g in h.domain if g.action == "context:read"),
        }
    )
    row = await h.agents.approve("org-a", "lead", approved)
    persisted = await h.db.managed_clients.find_one(
        {"org_id": "org-a", "client_id": row["client_id"]}
    )
    async with browser(h, "owner") as (client, headers):
        catalog = await client.get(BASE + "/catalog/templates?limit=1", headers=headers)
        assert catalog.status_code == 200, catalog.text
        impact = await client.get(BASE + "/templates/console-template/impact", headers=headers)
        assert impact.status_code == 200 and impact.json()["assigned_client_count"] == 1
        path = "/api/v6/organizations/org-a/client-templates/console-template"
        body = {
            "expected_revision": 1,
            "name": "Disabled",
            "scopes": ["context:read"],
            "status": "disabled",
        }
        assert (await client.put(path, headers=headers, json=body)).status_code == 200
        assert (await client.put(path, headers=headers, json=body)).status_code == 409
    assert (
        await h.db.managed_clients.find_one({"org_id": "org-a", "client_id": row["client_id"]})
        == persisted
    )
    async with browser(h, "lead") as (client, headers):
        assert (
            await client.get(BASE + "/templates/console-template/impact", headers=headers)
        ).status_code == 403
        assert (await client.get(BASE + "/members/other", headers=headers)).status_code == 403


async def test_binding_revision_and_cursor_filters_use_actual_mongo(managed_agents_db):
    h = managed_agents_db
    # The fixture has an explicitly reviewed projects:write bootstrap grant.
    async with browser(h, "owner") as (client, headers):
        body = {
            "expected_revision": 0,
            "project_id": "payment-service",
            "binding_type": "workspace_alias",
            "value": "fictional-console",
        }
        denied = await client.put(BASE + "/bindings/console-binding", headers=headers, json=body)
        assert denied.status_code == 200, denied.text
        assert (
            await client.put(BASE + "/bindings/console-binding", headers=headers, json=body)
        ).status_code == 409
        body.update(expected_revision=1, value="changed-fictional-console")
        assert (
            await client.put(BASE + "/bindings/console-binding", headers=headers, json=body)
        ).status_code == 200
        assert (
            await client.post(
                BASE + "/bindings/console-binding/delete",
                headers=headers,
                json={"expected_revision": 1},
            )
        ).status_code == 409
        assert (
            await client.post(
                BASE + "/bindings/console-binding/delete",
                headers=headers,
                json={"expected_revision": 2},
            )
        ).status_code == 200
    async with browser(h, "member") as (client, headers):
        page = await client.get(BASE + "/catalog/clients?limit=1", headers=headers)
        assert page.status_code == 200, page.text
        assert page.json()["items"] == []
        denied = await client.post(
            BASE + "/clients/foreign/sessions/foreign/end",
            headers=headers,
            json={"expected_revision": 1},
        )
        assert denied.status_code == 403


async def test_machine_catalog_filter_cursor_and_own_invitations_are_tenant_bound(
    managed_agents_db,
):
    h = managed_agents_db
    await h.agents.approve("org-a", "lead", approval(h))
    beta = approval(h).model_copy(
        update={
            "client_id": "beta-worker",
            "subject_id": "other",
            "parent_id": h.root,
            "expected_parent_generation": 3,
            "team_id": "beta",
        }
    )
    await h.agents.approve("org-a", "owner", beta)
    async with browser(h, "owner") as (client, headers):
        first = await client.get(BASE + "/catalog/clients?limit=1", headers=headers)
        assert first.status_code == 200, first.text
        cursor = first.json()["next_cursor"]
        assert cursor
        changed = await client.get(
            BASE + "/catalog/clients",
            headers=headers,
            params={"limit": 1, "cursor": cursor, "prefix": "managed"},
        )
        assert changed.status_code == 409
    async with browser(h, "lead") as (client, headers):
        own = await client.get(BASE + "/catalog/clients", headers=headers)
        assert [r["client_id"] for r in own.json()["items"]] == ["managed-worker"]
        foreign = await client.get(BASE + "/catalog/clients?prefix=beta", headers=headers)
        assert foreign.json()["items"] == [] and foreign.json()["next_cursor"] is None
    async with browser(h, "member") as (client, headers):
        own = await client.get(BASE + "/own-invitations", headers=headers)
        assert own.status_code == 200 and own.json()["items"] == []
        assert "token_hash" not in own.text and "password" not in own.text


async def test_machine_session_end_is_client_project_revision_and_audit_bound(managed_agents_db):
    from v6_managed_agent_fixtures import provision

    from app.security.tenant import TenantContext, tenant_scope
    from app.security.tenant_policy import policy_scope

    h = managed_agents_db
    _, _, delivered = await provision(h)
    principal = await h.app.state.auth.authenticate(delivered["token"])
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(principal.managed_policy, "session:write"),
    ):
        session = await h.app.state.agent_session_service.begin(
            principal, project_id="payment-service"
        )
    async with browser(h, "owner") as (client, headers):
        path = BASE + f"/clients/managed-worker/sessions/{session.session_id}/end"
        rows = await client.get(
            BASE + "/catalog/sessions?client_id=managed-worker", headers=headers
        )
        assert rows.status_code == 200, rows.text
        assert [r["session_id"] for r in rows.json()["items"]] == [session.session_id]
        assert "metadata" not in rows.json()["items"][0]
        stale = await client.post(path, headers=headers, json={"expected_revision": 999})
        assert stale.status_code == 409
        ended = await client.post(
            path, headers=headers, json={"expected_revision": session.revision}
        )
        assert ended.status_code == 200, ended.text
        stored = await h.db.agent_sessions.find_one(
            {"org_id": "org-a", "session_id": session.session_id}
        )
        assert stored["status"] == "ended" and stored["revision"] == session.revision + 1
        assert (
            await h.db.governance_events.count_documents(
                {"org_id": "org-a", "action": "machine_session_ended"}
            )
            == 1
        )
