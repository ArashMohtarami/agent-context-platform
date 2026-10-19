"""Release gaps exercised against the real adopted replica set, never filtered mocks."""

import asyncio
import json
from datetime import timedelta

import pytest
from v6_managed_agent_fixtures import approval, provision

from app.managed_agents.models import CredentialIssue
from app.management.memberships import Memberships
from app.models.common import utc_now
from app.security.keys import parse
from app.security.policy.evaluation import PolicyDenied
from tests.integration.test_v6_policy_fencing import browser

pytestmark = pytest.mark.integration


async def test_sponsor_suspend_fences_concurrent_key_issue_without_orphan_secret(
    managed_agents_db, monkeypatch
):
    h = managed_agents_db
    row = await h.agents.approve("org-a", "lead", approval(h))
    command = CredentialIssue(
        issuance_id="race-sponsor-suspend",
        expected_client_revision=row["revision"],
        tuples=h.domain,
        expires_at=utc_now() + timedelta(hours=1),
    )
    before = await h.db.api_keys.count_documents({})
    original = h.engine.fence
    ready, release = asyncio.Event(), asyncio.Event()
    observations = []

    async def pause(anchor, tx, *, mutation=False):
        if asyncio.current_task().get_name() == "acceptance-key-issue":
            observations.append(anchor["revision"])
            if len(observations) == 1:
                ready.set()
                await asyncio.wait_for(release.wait(), 10)
        await original(anchor, tx, mutation=mutation)

    monkeypatch.setattr(h.engine, "fence", pause)
    issuing = asyncio.create_task(
        h.agents.issue("org-a", "member", row["client_id"], command),
        name="acceptance-key-issue",
    )
    try:
        await asyncio.wait_for(ready.wait(), 10)
        membership = await h.db.organization_memberships.find_one({"_id": "membership-member"})
        await Memberships(h.engine).change_status(
            "org-a", "owner", "member", membership["revision"], "suspended"
        )
    finally:
        release.set()
    with pytest.raises(PolicyDenied):
        await asyncio.wait_for(issuing, 10)
    assert observations  # paused an actual transaction, not a pre-validation refusal
    assert await h.db.api_keys.count_documents({}) == before
    assert await h.db.managed_credentials.count_documents({"issuance_id": command.issuance_id}) == 0
    assert (
        await h.db.governance_events.count_documents({"action": "managed_credential_issued"}) == 0
    )
    membership = await h.db.organization_memberships.find_one({"_id": "membership-member"})
    await Memberships(h.engine).change_status(
        "org-a", "owner", "member", membership["revision"], "active"
    )
    with pytest.raises(PolicyDenied):
        await h.agents.issue("org-a", "member", row["client_id"], command)
    assert await h.db.api_keys.count_documents({}) == before


async def test_credential_delivery_has_no_plaintext_in_any_persisted_collection_or_log(
    managed_agents_db, caplog
):
    h = managed_agents_db
    row, command, delivered = await provision(h)
    token = delivered["token"]
    secret = parse(token).secret
    async with browser(h, "member") as (client, headers):
        response = await client.post(
            f"/api/v6/organizations/org-a/managed-clients/{row['client_id']}/credentials",
            headers=headers,
            json=command.model_dump(mode="json"),
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["delivered"] is False and "token" not in response.json()
    # Scan all state, including future collections; the earlier focused test
    # deliberately checked only six known receipt/audit collections.
    scanned = set()
    for name in await h.db.list_collection_names():
        rows = await h.db[name].find({}).to_list(5000)
        assert await h.db[name].count_documents({}) == len(rows), "fixture scan must be complete"
        persisted = json.dumps(rows, default=str)
        assert token not in persisted and secret not in persisted, name
        scanned.add(name)
    assert {"api_keys", "managed_credentials", "governance_outbox", "browser_sessions"} <= scanned
    assert token not in caplog.text and secret not in caplog.text


@pytest.mark.parametrize("stage", ["after_aggregation", "before_confirmation", "retention_narrow"])
async def test_monitoring_revoke_during_read_or_final_cas_never_returns_stale_totals(
    summary_db, monkeypatch, stage
):
    from v6_summary_fixtures import params

    from app.security.policy.engine import PolicyEngine
    from app.summaries.service import Summaries

    h = summary_db
    ready, release = asyncio.Event(), asyncio.Event()
    if stage in {"after_aggregation", "retention_narrow"}:
        original = Summaries.aggregate

        async def pause(self, *args, **kwargs):
            result = await original(self, *args, **kwargs)
            assert result["observed_count"] > 0
            ready.set()
            await asyncio.wait_for(release.wait(), 10)
            return result

        monkeypatch.setattr(Summaries, "aggregate", pause)
    else:
        original = PolicyEngine.confirm_read

        async def pause(self, anchor):
            ready.set()
            await asyncio.wait_for(release.wait(), 10)
            await original(self, anchor)

        monkeypatch.setattr(PolicyEngine, "confirm_read", pause)
    async with browser(h, "lead") as (client, headers):
        query = h.query.model_copy(update={"scope": "team", "team_id": "alpha"})
        reading = asyncio.create_task(
            client.get(
                "/api/v6/organizations/org-a/overview", params=params(query), headers=headers
            )
        )
        try:
            await asyncio.wait_for(ready.wait(), 10)
            if stage == "retention_narrow":
                from app.security.tenant import TenantContext, tenant_scope
                from app.summaries.operator import checkpoint

                with tenant_scope(TenantContext("org-a")):
                    await checkpoint(
                        h.engine,
                        "org-a",
                        h.declaration.model_copy(
                            update={"retained_from": h.begin + timedelta(minutes=2)}
                        ),
                    )
            else:
                member = await h.db.organization_memberships.find_one({"_id": "membership-lead"})
                await Memberships(h.engine).change_status(
                    "org-a", "owner", "lead", member["revision"], "suspended"
                )
        finally:
            release.set()
        response = await asyncio.wait_for(reading, 10)
        assert response.status_code in (403, 409), response.text
        assert "observed_count" not in response.text and "PRIVATE" not in response.text
        if stage == "retention_narrow":
            fresh = await client.get(
                "/api/v6/organizations/org-a/overview", params=params(query), headers=headers
            )
            assert fresh.status_code == 200 and fresh.json()["observed_count"] == 1
