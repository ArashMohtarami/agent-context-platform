"""Real recipient-only one-time delivery, secret-safe retry and copy-on-assignment."""

import asyncio
import json
from datetime import timedelta

import pytest
from v6_managed_agent_fixtures import approval, provision

from app.managed_agents.models import CredentialIssue, TemplatePut
from app.models.common import utc_now
from app.security.keys import matches, parse
from app.security.policy.evaluation import PolicyDenied
from app.security.principal import AuthenticationError
from tests.integration.test_v6_policy_fencing import browser

pytestmark = pytest.mark.integration


async def test_http_recipient_only_token_once_and_no_plaintext_persistence(managed_agents_db):
    h = managed_agents_db
    async with browser(h, "lead") as (client, headers):
        preview = await client.post(
            "/api/v6/organizations/org-a/managed-clients/preview",
            headers=headers,
            json=approval(h).model_dump(mode="json"),
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["rate_limit_profile_enforced"] is False
        assert await h.db.managed_clients.count_documents({}) == 0
        approved = await client.post(
            "/api/v6/organizations/org-a/managed-clients",
            headers=headers,
            json=approval(h).model_dump(mode="json"),
        )
        assert approved.status_code == 201, approved.text
        command = CredentialIssue(
            issuance_id="recipient-one",
            expected_client_revision=1,
            tuples=h.domain,
            expires_at=utc_now() + timedelta(hours=1),
        )
        refused = await client.post(
            "/api/v6/organizations/org-a/managed-clients/managed-worker/credentials",
            headers=headers,
            json=command.model_dump(mode="json"),
        )
        assert refused.status_code == 403 and "token" not in refused.text
    async with browser(h, "member") as (client, headers):
        first = await client.post(
            "/api/v6/organizations/org-a/managed-clients/managed-worker/credentials",
            headers=headers,
            json=command.model_dump(mode="json"),
        )
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == "no-store"
        token = first.json()["token"]
        key_id = first.json()["key_id"]
        second = await client.post(
            "/api/v6/organizations/org-a/managed-clients/managed-worker/credentials",
            headers=headers,
            json=command.model_dump(mode="json"),
        )
        assert (
            second.status_code == 200
            and second.json()["delivered"] is False
            and "token" not in second.json()
        )
    key = await h.db.api_keys.find_one({"org_id": "org-a", "key_id": key_id})
    assert matches(parse(token).secret, key["key_hash"])
    for collection in (
        "managed_credentials",
        "governance_events",
        "governance_outbox",
        "gateway_idempotency",
        "agent_activity",
        "audit_logs",
    ):
        text = json.dumps(await h.db[collection].find({}).to_list(2000), default=str)
        assert token not in text and parse(token).secret not in text
    async with browser(h, "lead") as (client, headers):
        response = await client.get(
            "/api/v6/organizations/org-a/managed-clients/managed-worker", headers=headers
        )
        assert (
            response.status_code == 200
            and token not in response.text
            and "key_hash" not in response.text
        )


async def test_explicit_rotation_lost_delivery_and_revocation(managed_agents_db):
    h = managed_agents_db
    row, command, first = await provision(h)
    replay = await h.agents.issue("org-a", "member", row["client_id"], command)
    assert "token" not in replay
    replacement = await h.agents.issue(
        "org-a",
        "member",
        row["client_id"],
        command.model_copy(
            update={"issuance_id": "replacement", "replaces_key_id": first["key_id"]}
        ),
    )
    with pytest.raises(AuthenticationError):
        await h.app.state.auth.authenticate(first["token"])
    assert await h.app.state.auth.authenticate(replacement["token"])
    await h.agents.revoke("org-a", "lead", row["client_id"], 1, key_id=replacement["key_id"])
    with pytest.raises(AuthenticationError):
        await h.app.state.auth.authenticate(replacement["token"])


async def test_template_disable_does_not_propagate_and_explicit_assignment_narrows(
    managed_agents_db,
):
    h = managed_agents_db
    await h.agents.put_template(
        "org-a",
        "owner",
        "fictional-template",
        TemplatePut(
            expected_revision=0,
            name="Fictional",
            scopes=tuple(sorted({g.action for g in h.domain})),
        ),
    )
    row, command, first = await provision(
        h, profile_id="fictional-template", expected_profile_revision=1
    )
    from app.models.job_authority import JobAuthority
    from app.security.job_authority import MongoJobAuthorityChecker
    from app.security.tenant import TenantContext, tenant_scope
    from app.security.tenant_policy import policy_scope

    principal = await h.app.state.auth.authenticate(first["token"])
    with (
        tenant_scope(TenantContext("org-a")),
        policy_scope(principal.managed_policy, "session:write"),
    ):
        operational = await h.app.state.agent_session_service.begin(
            principal, project_id="payment-service"
        )
    authority = JobAuthority(
        org_id="org-a",
        key_id=first["key_id"],
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
    before = await h.db.agent_clients.find_one({"org_id": "org-a", "client_id": row["client_id"]})
    await h.agents.put_template(
        "org-a",
        "owner",
        "fictional-template",
        TemplatePut(
            expected_revision=1,
            name="Disabled",
            scopes=("context:read",),
            status="disabled",
            rate_limit_profile="descriptive-only",
        ),
    )
    assert await h.db.agent_clients.find_one({"_id": before["_id"]}) == before
    refreshed = await h.app.state.auth.authenticate(first["token"])
    assert refreshed.managed_policy.fingerprint == principal.managed_policy.fingerprint
    with tenant_scope(TenantContext("org-a")), policy_scope(refreshed.managed_policy, "agent:self"):
        assert await h.app.state.agent_session_service.require_active_for_gateway(
            refreshed, operational.session_id
        )
        await MongoJobAuthorityChecker(h.db, worker_schema_version=42).check(authority)
    with pytest.raises(PolicyDenied):
        await h.agents.approve(
            "org-a",
            "lead",
            approval(h).model_copy(
                update={
                    "expected_revision": 1,
                    "profile_id": "fictional-template",
                    "expected_profile_revision": 2,
                }
            ),
        )
    await h.agents.put_template(
        "org-a",
        "owner",
        "fictional-template",
        TemplatePut(expected_revision=2, name="Narrow", scopes=("context:read",)),
    )
    narrow = approval(h).model_copy(
        update={
            "expected_revision": 1,
            "profile_id": "fictional-template",
            "expected_profile_revision": 3,
            "tuples": tuple(g for g in h.domain if g.action == "context:read"),
        }
    )
    await h.agents.approve("org-a", "lead", narrow)
    with pytest.raises(AuthenticationError):
        await h.app.state.auth.authenticate(first["token"])
    with pytest.raises(PolicyDenied):
        await h.agents.issue(
            "org-a",
            "member",
            row["client_id"],
            command.model_copy(update={"issuance_id": "stale-delivery"}),
        )


async def test_concurrent_issuance_and_audit_failure_are_secret_safe(managed_agents_db):
    h = managed_agents_db
    row = await h.agents.approve("org-a", "lead", approval(h))
    command = CredentialIssue(
        issuance_id="racing",
        expected_client_revision=1,
        tuples=h.domain,
        expires_at=utc_now() + timedelta(hours=1),
    )
    results = await asyncio.gather(
        *(h.agents.issue("org-a", "member", row["client_id"], command) for _ in range(2)),
        return_exceptions=True,
    )
    assert sum(isinstance(x, dict) and "token" in x for x in results) == 1
    assert await h.db.managed_credentials.count_documents({"issuance_id": "racing"}) == 1
    original = h.engine.audit

    async def fail(*args, **kwargs):
        raise RuntimeError("fictional-audit-outage")

    h.engine.audit = fail
    before = await h.db.api_keys.count_documents({})
    try:
        with pytest.raises(RuntimeError):
            await h.agents.issue(
                "org-a",
                "member",
                row["client_id"],
                command.model_copy(update={"issuance_id": "audit-refused"}),
            )
    finally:
        h.engine.audit = original
    assert await h.db.api_keys.count_documents({}) == before
    assert not await h.db.managed_credentials.find_one({"issuance_id": "audit-refused"})


async def test_expiry_and_current_client_resource_narrowing_are_enforced(managed_agents_db):
    h = managed_agents_db
    row, _, first = await provision(h)
    await h.db.agent_clients.update_one(
        {"org_id": "org-a", "client_id": row["client_id"]},
        {"$set": {"project_mode": "none", "project_ids": []}, "$inc": {"revision": 1}},
    )
    principal = await h.app.state.auth.authenticate(first["token"])
    assert not any(
        g.resource_kind == "project" and g.mode != "none" for g in principal.managed_policy.grants
    )
    assert any(g.resource_kind == "skill" for g in principal.managed_policy.grants)
    key = await h.db.api_keys.find_one({"key_id": first["key_id"]})
    await h.db.api_keys.update_one(
        {"_id": key["_id"]}, {"$set": {"expires_at": key["created_at"] + timedelta(milliseconds=1)}}
    )
    with pytest.raises(AuthenticationError):
        await h.app.state.auth.authenticate(first["token"])
